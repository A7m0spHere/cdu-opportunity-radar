"""M10-C: short local checks; live collection is explicitly capped at 2-3 rounds."""
import argparse
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
import logging
from pathlib import Path
import queue as queues
import socket
import sqlite3
import subprocess
import sys
import threading
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload
from cdu_radar.async_queue import JobQueue, source_snapshot
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.http import Fetcher
from cdu_radar.models import Article
from cdu_radar.recheck import collect_cycle, initialize, recheck, stamp
from cdu_radar.rss_consumer import config, export_run, poll
from cdu_radar.rss_service import make_server
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store
from scripts.m10_probe import reader, snapshot, write_new


def logical_digests(path):
    """Read a consistent transaction, excluding SQLite file-header churn."""
    with reader(path) as db:
        names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = {name: hashlib.sha256(json.dumps([list(row) for row in db.execute(
            'SELECT * FROM "'+name.replace('"', '""')+'" ORDER BY rowid')],
            ensure_ascii=False, sort_keys=True).encode()).hexdigest() for name in names}
        schema = [list(row) for row in db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name')]
        result['__schema__'] = hashlib.sha256(json.dumps(
            [db.execute('PRAGMA user_version').fetchone()[0], schema], ensure_ascii=False).encode()).hexdigest()
        return result


def rows(path, table):
    with reader(path) as db:
        return [list(row) for row in db.execute('SELECT * FROM '+table+' ORDER BY rowid')]


@contextmanager
def serving(raw, state, *, jobs=None, analyzer=None, point=None, port=0):
    controller = AutoFeedController(raw, state, analysis_state=jobs, analysis_analyzer=analyzer,
                                    **({'clock': lambda: point[0], 'data_mode': 'offline_replay'} if point else {}))
    server = None; worker = None
    try:
        if not controller.refresh():
            raise RuntimeError('Initial publisher refresh failed')
        server = make_server(controller, port=port)
        worker = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
        worker.start()
        yield controller, f'http://127.0.0.1:{server.server_address[1]}'
    finally:
        if worker:
            server.shutdown(); worker.join(timeout=5)
        if server:
            server.server_close()
        controller.close()
        if worker and worker.is_alive():
            raise RuntimeError('Local HTTP thread did not stop')


def request(base, path, headers=None):
    with httpx.Client(timeout=5, trust_env=False) as client:
        return client.get(base+path, headers=headers)


def synthetic(root):
    root.mkdir(parents=True, exist_ok=False)
    raw, jobs, state, consumer = (root/name for name in ('notices.sqlite3', 'jobs.sqlite3', 'publisher', 'consumer.sqlite3'))
    point = [reference_datetime('2026-10-22T12:00:00+08:00')]
    analyzer, _ = configuration(None, True)
    official = next(source for source in load_sources() if source.id == 'computer')
    url = 'https://computer.cdu.edu.cn/info/1036/999801.htm'
    title = '合成本科生人工智能竞赛报名通知'
    content = '欢迎本科生报名人工智能编程竞赛，请于2026年10月30日前提交参赛材料，具体资格以官方原文为准。'
    # Exercise the actual list/detail parsing and storage pipeline against a
    # synthetic transport, never real official sites.
    def transport(req):
        if req.url.path == '/robots.txt':
            return httpx.Response(404)
        if '/info/' in req.url.path:
            html = f'<form name="_newscontent_fromname"><div class="div_title">{title}<div class="box">发布时间：2026年10月22日</div></div><div id="vsb_content_2"><p>{content}</p></div></form>'
        else:
            html = f'<div class="div_list"><div class="div_title"><div class="date">2026-10-22</div><a href="{url}">{title}</a></div></div>'
        return httpx.Response(200, text=html, headers={'content-type': 'text/html; charset=utf-8'})
    with Store(raw) as store:
        store.sync_sources(load_sources()); initialize(store.db)
        with Fetcher(transport=httpx.MockTransport(transport), interval=0, retries=0, event_sink=store.request_event) as fetcher:
            collected = collect_cycle(store, fetcher, [official], limit=1, recheck_limit=1, request_budget=12)
        assert collected['discovery'][0]['inserted'] == 1
    point[0] = reference_datetime() + timedelta(seconds=1)
    # Explicit synthetic proposals; these are never evidence of real AI quality.
    with JobQueue(jobs, raw) as job_queue:
        job_queue.scan(analyzer)
        with source_snapshot(raw) as repo:
            for ident in repo.ids():
                notice = repo.input(ident); supplied, _ = analyzer.prepare(notice)
                payload = mock_payload(supplied)
                payload['importance'] = dict(kind='inference', level='high', reason='合成故障验收', quotes=[notice.content])
                checked = analyzer.validate(payload, notice, supplied, point[0])
                claim = job_queue.claim(analyzer, [ident])
                assert claim is not None
                job_queue.record_event(claim, {'status_code': 200, 'usage':
                    {'prompt_tokens': 8, 'completion_tokens': 2, 'total_tokens': 10}})
                job_queue.finish(claim, result=checked)
    events = []
    with serving(raw, state, jobs=jobs, analyzer=analyzer, point=point) as (controller, base):
        def probe(name):
            result = snapshot(raw, state, analysis=jobs, consumer=consumer, base=base, now=point[0])
            write_new(root/(name+'.json'), result)
            return result
        healthy = probe('01-healthy')
        assert healthy['consistency'] == {'all': True, 'featured': True}
        assert len(healthy['http']['rss_featured']['guids']) == 1
        events.append(dict(case='collection_to_rules_synthetic_analysis_json_rss', result='passed'))
        subscriptions = config(dict(schema_version=1, subscriptions=[dict(name='合成验收', url=base+'/rss/v2/all.xml')]))
        initialized = poll(subscriptions, consumer)
        assert initialized['subscriptions'][0]['status'] == 'initialized'
        persistent = dict(publications=rows(raw, 'auto_publications'), first=rows(state/'subscriptions.sqlite3', 'first_featured'),
                          attempts=rows(jobs, 'attempts'), watermark=rows(consumer, 'consumer_items'))
        assert persistent['attempts'], 'Restart must preserve a nonempty synthetic usage ledger'
        old = request(base, '/rss/v2/all.xml'); etag = old.headers['etag']
        # A real SQLite exclusive lock must yield unavailable, not old data/304.
        locked = sqlite3.connect(raw, timeout=.2)
        try:
            locked.execute('BEGIN EXCLUSIVE')
            statuses = [request(base, path, {'If-None-Match': etag}).status_code
                        for path in ('/rss/v2/all.xml', '/api/notices/v4')]
            assert statuses == [503, 503]
        finally:
            locked.rollback(); locked.close()
        assert controller.refresh()
        events.append(dict(case='database_lock', statuses=statuses, recovered=True))
        with sqlite3.connect(jobs) as db:
            saved = db.execute('SELECT state,result FROM jobs').fetchone()
            db.execute("UPDATE jobs SET state='queued',result=NULL")
        pending = probe('02-analysis-backlog')
        assert pending['http']['rss_all']['status'] == 200
        assert pending['http']['rss_featured']['status'] == 503
        assert pending['http']['json_featured']['featured']['coverage']['complete'] == 0
        events.append(dict(case='analysis_backlog_incomplete_featured', all_status=200, featured_status=503))
        with sqlite3.connect(jobs) as db:
            db.execute('UPDATE jobs SET state=?,result=?', saved)
        assert controller.refresh()
        controller.subscription_budget.tokens = 0
        controller.subscription_budget.per_second = 0
        limited = request(base, '/rss/v2/all.xml')
        assert limited.status_code == 429
        controller.subscription_budget.tokens = 60
        controller.subscription_budget.per_second = 1
        events.append(dict(case='rate_limit', status=429))
        # Advance only the injected clock. This does not count as a 72h run.
        point[0] += timedelta(hours=73)
        stale = request(base, '/rss/v2/all.xml', {'If-None-Match': etag})
        assert stale.status_code == 503
        assert controller.refresh()
        expired = probe('03-simulated-73h')
        assert expired['raw']['health_older_than_72h'] == 1
        assert expired['raw']['rechecks']['due'] == 1
        assert expired['http']['rss_all']['guids'] == []
        events.append(dict(case='source_expiry_simulated_73h', stale_response=503, after_refresh_items=0))
        point[0] -= timedelta(hours=73)
        assert controller.refresh()
        # Two failed original checks at their scheduled retry times, not a
        # new discovery loop; all upstream responses remain synthetic.
        with Store(raw) as store:
            initialize(store.db)
            store.db.execute('UPDATE notice_rechecks SET next_check_at=?', (stamp(point[0]),)); store.db.commit()
            def unavailable(req):
                return httpx.Response(404 if req.url.path == '/robots.txt' else 503)
            with Fetcher(transport=httpx.MockTransport(unavailable), interval=0, retries=0) as fetcher:
                one = recheck(store, fetcher, [official], limit=1, clock=lambda: point[0])
                point[0] += timedelta(hours=1)
                two = recheck(store, fetcher, [official], limit=1, clock=lambda: point[0])
            check = dict(store.db.execute('SELECT * FROM notice_rechecks').fetchone())
            assert one[0]['result'] == two[0]['result'] == 'failed' and check['failures'] == 2
        assert request(base, '/rss/v2/all.xml', {'If-None-Match': etag}).status_code == 503
        assert controller.refresh()
        failed = probe('04-source-failure')
        assert failed['http']['rss_all']['guids'] == []
        events.append(dict(case='two_source_failures', failures=2, next_check_at=check['next_check_at'], stale_response=503))
        with Store(raw) as store:
            store.save_notice('computer', url, Article(title, '2026-10-22', content), seen_at=point[0].isoformat())
        assert controller.refresh()
        port = int(base.rsplit(':', 1)[1])
    with serving(raw, state, jobs=jobs, analyzer=analyzer, point=point, port=port) as (_, base):
        recovered = snapshot(raw, state, analysis=jobs, consumer=consumer, base=base, now=point[0])
        assert recovered['consistency'] == {'all': True, 'featured': True}
        assert rows(raw, 'auto_publications') == persistent['publications']
        assert rows(state/'subscriptions.sqlite3', 'first_featured') == persistent['first']
        assert rows(jobs, 'attempts') == persistent['attempts']
        assert rows(consumer, 'consumer_items') == persistent['watermark']
        result = poll(subscriptions, consumer)
        assert not result['subscriptions'][0]['drafts']
        export_run(consumer, root/'consumer-recovered', result['run_id'])
        write_new(root/'05-recovered.json', recovered)
    events.append(dict(case='publisher_stop_start_persistent_state', publications=True, first_featured=True,
                       attempts=True, consumer_watermark=True, duplicate_drafts=0))
    report = dict(mode='synthetic_local_faults', passed=True, events=events, official_requests=0,
                  model_requests=0, qq_deliveries=0, simulated_time_only=True, listeners_stopped=True)
    write_new(root/'result.json', report)
    return report


def process_start(raw, state, port):
    # -m retains normal package imports; collection is explicitly disabled.
    cmd = [sys.executable, '-m', 'cdu_radar', 'serve-rss', '--db', str(raw), '--state-dir', str(state),
           '--port', str(port), '--collect-interval', '0']
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding='utf-8', cwd=Path(__file__).resolve().parents[1])
    output = queues.Queue()
    threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True).start()
    try:
        line = output.get(timeout=15)
        if not line:
            raise RuntimeError('Publisher process did not start')
        started = json.loads(line)
        return process, 'http://127.0.0.1:'+str(started['port'])
    except BaseException:
        process_stop(process)
        raise


def wait_closed(base):
    deadline = time.monotonic()+5
    while True:
        with socket.socket() as check:
            check.settimeout(.2)
            closed = check.connect_ex(('127.0.0.1', int(base.rsplit(':', 1)[1]))) != 0
        if closed:
            return
        if time.monotonic() >= deadline:
            raise RuntimeError('Own publisher listener did not close after process exit')
        time.sleep(.05)


def process_stop(process, base=None):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)
    process.stdout.close()
    if base is not None:
        wait_closed(base)


def live(root, rounds):
    if rounds not in {2, 3}:
        raise ValueError('Live collection is limited to exactly 2 or 3 rounds')
    root.mkdir(parents=True, exist_ok=False)
    raw, jobs, state, consumer = (root/name for name in ('notices.sqlite3', 'jobs.sqlite3', 'publisher', 'consumer.sqlite3'))
    catalog = load_sources()
    sources = [source for source in catalog if source.status == 'verified' and source.parser_supported and source.adapter]
    with Store(raw) as store:
        store.sync_sources(catalog); initialize(store.db)
    with JobQueue(jobs, raw):
        pass  # Explicit empty private queue; no configuration, key or model work.
    reports = []
    for number in range(1, rounds+1):
        started = time.monotonic(); stop = threading.Event()
        timer = threading.Timer(180, stop.set); timer.daemon = True; timer.start()
        try:
            with Store(raw) as store:
                initialize(store.db)
                with Fetcher(timeout=10, retries=0, interval=2.5, stop_event=stop,
                             event_sink=store.request_event) as fetcher:
                    cycle = collect_cycle(store, fetcher, sources, limit=1, recheck_limit=2,
                                          request_budget=48, stop_event=stop)
        finally:
            timer.cancel()
        with serving(raw, state) as (_, base):
            observed = snapshot(raw, state, analysis=jobs, base=base)
        assert cycle['requests'] <= 48 and observed['consistency']['all'] is True
        report = dict(round=number, elapsed_seconds=round(time.monotonic()-started, 3),
                      deadline_reached=stop.is_set(), collection=cycle, probe=observed)
        write_new(root/f'round-{number}.json', report); reports.append(report)
        print(json.dumps(dict(round=number, requests=cycle['requests'], raw=observed['raw']['counts']['notices'],
                              all_status=observed['http']['rss_all']['status'],
                              featured_status=observed['http']['rss_featured']['status'])), flush=True)
    return recover_live(root, reports)


def recover_live(root, reports):
    """Resume only process recovery from saved rounds; performs no collection."""
    rounds = len(reports)
    if rounds not in {2, 3} or [item['round'] for item in reports] != list(range(1, rounds+1)):
        raise ValueError('Recovery requires 2 or 3 completed, numbered round reports')
    if (root/'result.json').exists():
        raise ValueError('Recovery result already exists')
    raw, jobs, state, consumer = (root/name for name in ('notices.sqlite3', 'jobs.sqlite3', 'publisher', 'consumer.sqlite3'))
    port = 0
    if consumer.exists():
        with reader(consumer) as db:
            saved_url = db.execute('SELECT url FROM consumer_subscriptions ORDER BY identity LIMIT 1').fetchone()
        if saved_url:
            from urllib.parse import urlsplit
            port = urlsplit(saved_url[0]).port
    # A real subprocess is terminated and reopened at the same publisher URL.
    # Its collector is disabled, so this cannot create a fourth live round.
    process, base = process_start(raw, state, port)
    try:
        before = snapshot(raw, state, analysis=jobs, base=base)
        subscriptions = config(dict(schema_version=1, subscriptions=[dict(name='真实通知本机验收', url=base+'/rss/v2/all.xml')]))
        first = poll(subscriptions, consumer)
        assert first['subscriptions'][0]['status'] in {'initialized', 'ready', 'empty'}
        assert not first['subscriptions'][0]['drafts'], 'Recovery initialization produced unexpected drafts'
        publications = rows(raw, 'auto_publications'); attempts = rows(jobs, 'attempts')
        watermark = rows(consumer, 'consumer_items')
        before_data = {str(path): logical_digests(path) for path in (raw, jobs)}
    finally:
        process_stop(process, base)
    process, recovered_base = process_start(raw, state, int(base.rsplit(':', 1)[1]))
    try:
        after = snapshot(raw, state, analysis=jobs, consumer=consumer, base=recovered_base)
        repeated = poll(subscriptions, consumer)
        assert not repeated['subscriptions'][0]['drafts'], 'Restart generated duplicate drafts'
        assert before['http']['rss_all']['identities'] == after['http']['rss_all']['identities'], 'GUID/pubDate changed'
        assert publications == rows(raw, 'auto_publications') and attempts == rows(jobs, 'attempts'), 'Publication or usage rows changed'
        assert watermark == rows(consumer, 'consumer_items'), 'Consumer watermark changed'
        after_data = {str(path): logical_digests(path) for path in (raw, jobs)}
        changes = [table for path, tables in before_data.items() for table in tables
                   if after_data.get(path, {}).get(table) != tables[table]]
        assert before_data == after_data, 'Logical database state changed: '+','.join(changes)
        export_run(consumer, root/('restart-consumer-'+str(repeated['run_id'])), repeated['run_id'])
    finally:
        process_stop(process, recovered_base)
    result = dict(mode='bounded_real_collection', completed_rounds=rounds,
                  policy=dict(max_rounds=3, requests_per_round=48, max_total_requests=rounds*48,
                              discovery_requests_per_round=24, article_limit_per_source=1,
                              recheck_limit_per_round=2, interval_seconds=2.5, timeout_seconds=10,
                              retries=0, round_stop_after_seconds=180),
                  official_requests=sum(item['collection']['requests'] for item in reports),
                  raw_notices=after['raw']['counts']['notices'],
                  consistency=after['consistency'], featured_status=after['http']['rss_featured']['status'],
                  analysis_status='not_run_no_model_calls', model_requests=0, qq_deliveries=0,
                  publisher_process_restart=True, raw_and_jobs_logical_state_preserved=True,
                  guid_pubdate_preserved=True, consumer_duplicate_drafts=0, listeners_stopped=True,
                  real_48h_run='not_executed_server_phase', before_restart=before, after_restart=after)
    write_new(root/'result.json', result)
    return result


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True, type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--live-rounds', type=int, choices=(2, 3))
    mode.add_argument('--resume-recovery', action='store_true', help='Only recover saved rounds; no official requests')
    args = parser.parse_args()
    # Refuse an existing directory before adding logs or state.
    if args.directory.exists() and not args.resume_recovery:
        parser.error('Choose a new isolated directory')
    if not args.resume_recovery:
        args.directory.mkdir(parents=True, exist_ok=False)
    elif not args.directory.is_dir():
        parser.error('Recovery requires an existing local acceptance directory')
    logging.basicConfig(filename=args.directory/'private.log', encoding='utf-8', level=logging.INFO)
    if args.resume_recovery:
        root = args.directory/'live'
        reports = [json.loads(path.read_text('utf-8')) for path in sorted(root.glob('round-*.json'))]
        result = recover_live(root, reports)
    else:
        result = live(args.directory/'live', args.live_rounds) if args.live_rounds else synthetic(args.directory/'synthetic')
    print(json.dumps({key: value for key, value in result.items() if key not in {'before_restart', 'after_restart'}},
                     ensure_ascii=False, indent=2))
