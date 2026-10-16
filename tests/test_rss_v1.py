"""Issue #13: real loopback requests, synthetic originals and offline proposals."""
from dataclasses import replace
from datetime import timedelta
import json
import sqlite3
import threading
from xml.etree import ElementTree as ET

import feedparser
import pytest

from cdu_radar import rss_v1
from cdu_radar.auto_publish import failed, MAX_SOURCE_AGE
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.featured import FeaturedPolicy
from cdu_radar.lan_preview import make_preview_server, upstream_read
from cdu_radar.rss_service import make_server
from test_auto_publish import auto_service, NOW, URL, add
from test_featured import fill, raw_path
from test_rss_service import loopback_only, request

ALL, FEATURED = rss_v1.ROUTES


def setup_featured(service, tmp_path):
    store, controller, *_ = service
    analyzer, state, _ = fill(store, tmp_path)
    controller.data_mode = 'offline_replay'
    controller.analysis_analyzer, controller.analysis_state = analyzer, state
    assert controller.refresh() and controller.subscription_ready
    return analyzer, state


def parsed(server, path):
    status, headers, body = request(server, path)
    assert status == 200
    feed = feedparser.parse(body)
    assert not feed.bozo and feed.version == 'rss20'
    return feed, headers, body


def test_routes_filters_defaults_reader_and_legacy_compatibility(auto_service, tmp_path, monkeypatch):
    store, c, s, *_ = auto_service
    analyzer, state = setup_featured(auto_service, tmp_path)
    monkeypatch.setattr(type(analyzer), 'analyze', lambda *a, **kw: pytest.fail('HTTP model call'))
    monkeypatch.setattr(analyzer.provider, 'complete', lambda *a, **kw: pytest.fail('HTTP provider call'))
    old = {p: request(s, p)[2] for p in ['/feed.xml', '/current.xml', '/history.xml']}
    before = {p: p.read_bytes() for p in [raw_path(store), state, c.subscription_path]}
    all_feed, headers, _ = parsed(s, ALL)
    featured, _, body = parsed(s, FEATURED)
    assert len(all_feed.entries) == 7 and len(featured.entries) == 3
    assert headers['Cache-Control'] == 'no-store, max-age=0'
    assert request(s, FEATURED + '?source=all&category=all')[2] == body
    assert request(s, FEATURED + '?category=all&source=all')[2] == body
    assert request(s, FEATURED, method='HEAD')[2] == b''
    assert len(parsed(s, FEATURED + '?view=history')[0].entries) == 1
    assert len(parsed(s, FEATURED + '?type=competition&topic=computing,ai')[0].entries) == 1
    assert parsed(s, FEATURED + '?topic=ai,computing')[2] == parsed(s, FEATURED + '?topic=computing,ai')[2]
    assert len(parsed(s, ALL + '?source=business')[0].entries) == 1
    assert not parsed(s, FEATURED + '?source=business')[0].entries
    assert len(parsed(s, ALL + '?category=' + '%E5%AD%A6%E7%A7%91%E7%AB%9E%E8%B5%9B')[0].entries) == 1
    assert set(e.id for e in featured.entries) <= set(e.id for e in all_feed.entries)
    assert all(request(s, p)[2] == body for p, body in old.items())
    assert request(s, '/approved.xml')[0] == 410
    assert all(p.read_bytes() == body for p, body in before.items()), 'HTTP must be read-only'
    for secret in ['input_hash', 'lease_token', 'api_key', 'raw_model_response', 'state_db']:
        assert secret not in body.decode()
    assert ET.fromstring(body).findtext('./channel/{' + rss_v1.NS + '}analysis_state') == 'degraded'
    assert '分析覆盖不完整' in featured.feed.description


@pytest.mark.parametrize('query', [
    '?source=computer&source=all', '?source=', '?source=unverified', '?category=unknown',
    '?source=https://evil.invalid', '?file=../subscriptions.sqlite3', '?topic=ai,ai',
    '?topic=all,ai', '?selection=featured', '?view=featured', '?x=%zz', '?source=%FF',
    '?source=%E0%A4%A', '?%73ource=all&source=all', '?source=' + 'a' * 101,
    '?from=2026-10-32', '?from=2026-10-17&to=2026-10-01', '?published=unknown&from=2026-10-01',
    '?' + 'source=all&' * 10, '?source=' + 'a' * 2100,
])
def test_strict_query_validation(auto_service, query):
    # Parsing must not need configured/healthy model state.
    assert request(auto_service[2], ALL + query)[0] == 400
    assert request(auto_service[2], FEATURED + query)[0] == 400


@pytest.mark.parametrize('path', ['/rss/v1/no.xml', '/rss/v1/all.xml/', '/rss/v1/%61ll.xml',
    '/rss/v1/../all.xml', '/subscriptions.sqlite3', '/rss/v1/featured.xml#private'])
def test_exact_routes_and_no_private_files(auto_service, path):
    assert request(auto_service[2], path)[0] == 404


def test_first_not_ready_partial_and_healthy_empty_are_distinct(auto_service, tmp_path):
    _, c, s, *_ = auto_service
    assert request(s, FEATURED)[0] == 503
    assert request(s, ALL)[0] == 200
    _, state = setup_featured(auto_service, tmp_path)
    assert len(parsed(s, FEATURED)[0].entries) == 3
    with sqlite3.connect(state) as db:
        db.execute("UPDATE jobs SET state='queued',result=NULL")
    assert request(s, FEATURED)[0] == 503
    assert c.refresh()
    assert request(s, FEATURED)[0] == 503
    # A configured healthy empty cohort is a valid empty RSS; a nonempty
    # cohort with no completed analyses must never masquerade as empty.
    assert not parsed(s, FEATURED + '?type=research')[0].entries
    assert request(s, ALL)[0] == 200


def test_etag_is_checked_after_current_analysis_not_only_originals(auto_service, tmp_path):
    _, c, s, *_ = auto_service
    _, state = setup_featured(auto_service, tmp_path)
    _, headers, body = parsed(s, FEATURED)
    tag = headers['ETag']
    assert request(s, FEATURED, headers={'If-None-Match': 'W/' + tag})[0] == 304
    with sqlite3.connect(state) as db:
        db.execute("UPDATE jobs SET state='failed',error='timeout' WHERE notice_id=1")
    assert request(s, FEATURED, headers={'If-None-Match': tag})[0] == 503
    assert c.wakeup.is_set() and request(s, ALL)[0] == 200
    assert c.refresh()
    status, h, new = request(s, FEATURED, headers={'If-None-Match': tag})
    assert status == 200 and new != body and h['ETag'] != tag
    assert len(feedparser.parse(new).entries) == 2
    c.featured_policy = FeaturedPolicy(model_threshold='high', rule_threshold=4)
    assert request(s, FEATURED, headers={'If-None-Match': '*'})[0] == 503


@pytest.mark.parametrize('fault', ['changed', 'withdrawn', 'expired', 'catalog', 'database', 'validation_age'])
def test_every_projection_keeps_raw_gate_and_never_stale_304(auto_service, tmp_path, monkeypatch, fault):
    store, c, s, point, mono = auto_service
    setup_featured(auto_service, tmp_path)
    tags = {p: request(s, p)[1]['ETag'] for p in [ALL, FEATURED + '?source=computer']}
    if fault == 'changed':
        add(store, body='通知内容已经更新；旧的智能分析必须失效，请查看最新原文。')
    elif fault == 'withdrawn':
        with store.db:
            failed(store.db, URL, 'withdrawn', 'synthetic withdrawal', NOW.isoformat())
    elif fault == 'expired':
        point[0] += MAX_SOURCE_AGE + timedelta(seconds=1)
    elif fault == 'catalog':
        import cdu_radar.auto_publish as module
        catalog = module.load_sources()
        monkeypatch.setattr(module, 'load_sources', lambda: [replace(i, status='pending') if i.id == 'computer' else i for i in catalog])
    elif fault == 'database':
        monkeypatch.setattr(c, 'db', tmp_path / 'missing.sqlite3')
    else:
        mono[0] = 31
    for path, tag in tags.items():
        status, headers, body = request(s, path, headers={'If-None-Match': tag})
        assert status == 503 and 'ETag' not in headers and b'<rss' not in body


@pytest.mark.parametrize('fault', ['missing_queue', 'locked_queue', 'queue_wal', 'corrupt_result', 'collection_disk', 'collection_wal'])
def test_private_faults_fail_closed_but_all_information_remains(auto_service, tmp_path, fault):
    _, c, s, *_ = auto_service
    _, state = setup_featured(auto_service, tmp_path)
    tag = request(s, FEATURED)[1]['ETag']
    locker = None
    try:
        if fault == 'missing_queue':
            c.analysis_state = tmp_path / 'missing.sqlite3'
        elif fault == 'locked_queue':
            locker = sqlite3.connect(state)
            locker.execute('BEGIN EXCLUSIVE')
        elif fault == 'queue_wal':
            with sqlite3.connect(state) as db:
                db.execute('PRAGMA journal_mode=WAL')
        elif fault == 'corrupt_result':
            with sqlite3.connect(state) as db:
                db.execute("UPDATE jobs SET result='not json' WHERE notice_id=1")
        elif fault == 'collection_disk':
            c.subscription_path.write_bytes(b'not sqlite')
        elif fault == 'collection_wal':
            with sqlite3.connect(c.subscription_path) as db:
                db.execute('PRAGMA journal_mode=WAL')
        assert request(s, FEATURED, headers={'If-None-Match': tag})[0] == 503
        assert request(s, ALL)[0] == 200 and request(s, '/feed.xml')[0] == 200
    finally:
        if locker:
            locker.rollback()
            locker.close()


def test_global_first_featured_stable_across_filters_reselection_update_restart(auto_service, tmp_path):
    store, c, s, point, _ = auto_service
    point[0] += timedelta(minutes=2)
    analyzer, state = setup_featured(auto_service, tmp_path)
    initial = {e.id: e.published for e in parsed(s, FEATURED)[0].entries}
    all_times = {e.id: e.published for e in parsed(s, ALL)[0].entries}
    guid = next(e.id for e in parsed(s, ALL)[0].entries if e.link == URL)
    assert initial[guid] != all_times[guid]
    for entry in parsed(s, FEATURED + '?source=computer')[0].entries:
        assert entry.published == initial[entry.id]
    point[0] += timedelta(minutes=5)
    with sqlite3.connect(state) as db:
        result = db.execute('SELECT result FROM jobs WHERE notice_id=1').fetchone()[0]
        db.execute("UPDATE jobs SET state='failed' WHERE notice_id=1")
    assert c.refresh() and len(parsed(s, FEATURED)[0].entries) == 2
    with sqlite3.connect(state) as db:
        db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=1', (json.loads(result)['state'], result))
    assert c.refresh()
    assert {e.id: e.published for e in parsed(s, FEATURED)[0].entries} == initial
    assert {e.id: e.published for e in parsed(s, ALL)[0].entries} == all_times
    # A newly validated result for a changed original reuses the global first
    # featured time and URL-derived GUID, rather than inventing a new entry.
    from cdu_radar.analysis_store import AnalysisRepository
    from cdu_radar.async_queue import JobQueue
    from cdu_radar.async_contract import mock_payload, validate
    row = store.db.execute('SELECT title,content FROM notices WHERE id=1').fetchone()
    add(store, title=row['title'] + '（更新）', body=row['content'], at=point[0])
    assert request(s, FEATURED)[0] == 503
    repo = AnalysisRepository.__new__(AnalysisRepository); repo.store = store; repo.db = store.db
    notice = repo.input(1)
    supplied, _ = analyzer.prepare(notice)
    proposal = mock_payload(supplied)
    proposal['importance'] = {'kind': 'inference', 'level': 'high', 'reason': 'synthetic update', 'quotes': [notice.content]}
    checked = validate(proposal, notice, supplied, analyzer, point[0])
    with JobQueue(state, raw_path(store)) as queue:
        queue.scan(analyzer)
        queue.db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=1 AND input_hash=?',
                         (checked['state'], json.dumps(checked), notice.input_hash))
    assert c.refresh()
    assert {e.id: e.published for e in parsed(s, FEATURED)[0].entries} == initial
    # Same owned state directory is the durable identity ledger.
    root = c.lease.root
    c.close()
    restarted = AutoFeedController(raw_path(store), root, clock=lambda: point[0], data_mode='offline_replay',
                                   analysis_state=state, analysis_analyzer=analyzer)
    other = make_server(restarted, port=0)
    thread = threading.Thread(target=other.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    try:
        assert restarted.snapshot is None and not restarted.subscription_ready
        assert restarted.refresh()
        thread.start()
        assert {e.id: e.published for e in parsed(other, FEATURED)[0].entries} == initial
    finally:
        if thread.is_alive():
            other.shutdown(); thread.join(3)
        other.server_close(); restarted.close()


def test_publisher_collection_failure_disables_only_featured(auto_service, tmp_path, monkeypatch):
    _, c, s, *_ = auto_service
    setup_featured(auto_service, tmp_path)
    tag = request(s, FEATURED)[1]['ETag']
    from cdu_radar import subscription_store
    def fail(*args, **kwargs):
        raise OSError('synthetic disk failure')
    monkeypatch.setattr(subscription_store, 'persist', fail)
    assert c.refresh() and not c.subscription_ready
    assert request(s, FEATURED, headers={'If-None-Match': tag})[0] == 503
    assert request(s, ALL)[0] == 200 and request(s, '/feed.xml')[0] == 200


def test_reanalysis_same_decision_invalidates_collection_before_conditional(auto_service, tmp_path):
    _, c, s, *_ = auto_service
    _, state = setup_featured(auto_service, tmp_path)
    tag = request(s, FEATURED)[1]['ETag']
    with sqlite3.connect(state) as db:
        db.execute('UPDATE jobs SET updated_at=updated_at+1 WHERE notice_id=1')
    assert request(s, FEATURED, headers={'If-None-Match': tag})[0] == 503
    assert c.refresh()
    assert request(s, FEATURED, headers={'If-None-Match': tag})[0] == 304


def test_budget_is_shared_across_filters_and_refills(auto_service):
    _, c, s, _, mono = auto_service
    c.subscription_budget = rss_v1.RequestBudget(c.monotonic, burst=2, per_second=1)
    assert request(s, ALL)[0] == 200
    assert request(s, ALL + '?source=computer')[0] == 200
    status, headers, body = request(s, FEATURED)
    assert status == 429 and headers['Retry-After'] == '1' and b'<rss' not in body
    assert request(s, '/feed.xml')[0] == 200
    mono[0] += 1
    assert request(s, ALL, method='HEAD')[0] == 200


def test_only_read_methods_and_no_credentials_required(auto_service):
    s = auto_service[2]
    for method in ['POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']:
        assert request(s, ALL, method=method)[0] == 405
    assert request(s, ALL, headers={'User-Agent': 'ordinary-rss-reader'})[0] == 200


def test_item_byte_bounds_and_safe_xml(auto_service, monkeypatch):
    _, c, s, *_ = auto_service
    payload = dict(c.snapshot.data_v2)
    template = payload['items'][0]
    payload['items'] = [dict(template, guid=f'synthetic-{i}', title='<test> & 中文\x00', summary='原文 <script> & "中文"') for i in range(105)]
    body = rss_v1.render(payload, ALL)
    channel = ET.fromstring(body).find('channel')
    assert len(channel.findall('item')) == 100
    assert channel.findtext('{' + rss_v1.NS + '}truncated') == 'true'
    assert channel.findtext('{' + rss_v1.NS + '}total') == '105'
    assert channel.findtext('./item/title') == '<test> & 中文'
    assert '&lt;script&gt;' in channel.findtext('./item/description')
    monkeypatch.setattr(rss_v1, 'MAX_BYTES', 1)
    assert request(s, ALL)[0] == 503


def test_loopback_proxy_new_routes_conditionals_and_429(auto_service, tmp_path):
    _, c, s, *_ = auto_service
    setup_featured(auto_service, tmp_path)
    path = FEATURED + '?source=computer'
    status, body, _, tag = upstream_read(s.server_address[1], path)
    assert status == 200 and len(feedparser.parse(body).entries) == 3
    assert upstream_read(s.server_address[1], path, tag)[0] == 304
    assert upstream_read(s.server_address[1], ALL + '?source=invalid')[0] == 400
    c.subscription_budget = rss_v1.RequestBudget(c.monotonic, burst=1)
    proxy = make_preview_server(s.server_address[1])
    thread = threading.Thread(target=proxy.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
    thread.start()
    try:
        status, h, body = request(proxy, path)
        assert status == 200 and feedparser.parse(body).entries
        status, h, body = request(proxy, path)
        assert status == 429 and h['Retry-After'] == '1'
    finally:
        proxy.shutdown(); proxy.server_close(); thread.join(3)


def test_analysis_write_cannot_commit_during_featured_response(auto_service, tmp_path, monkeypatch):
    _, c, s, *_ = auto_service
    _, state = setup_featured(auto_service, tmp_path)
    from cdu_radar import subscription_store
    original = subscription_store.read
    blocked = []

    def checked(*args, **kwargs):
        first = original(*args, **kwargs)
        with sqlite3.connect(state, timeout=0) as writer:
            writer.execute('BEGIN IMMEDIATE')
            writer.execute("UPDATE jobs SET state='failed' WHERE notice_id=1")
            with pytest.raises(sqlite3.OperationalError, match='locked'):
                writer.commit()
            blocked.append(True)
            writer.rollback()
        return first

    monkeypatch.setattr(subscription_store, 'read', checked)
    assert request(s, FEATURED)[0] == 200 and blocked == [True]


def test_connection_slots_bound_threads_and_return_503(auto_service):
    s = auto_service[2]
    # Occupied handler slots cannot create an unbounded number of workers.
    for _ in range(8):
        assert s.slots.acquire(blocking=False)
    try:
        status, headers, _ = request(s, ALL)
        assert status == 503 and headers['Retry-After'] == '1'
    finally:
        for _ in range(8):
            s.slots.release()
    assert request(s, ALL)[0] == 200
