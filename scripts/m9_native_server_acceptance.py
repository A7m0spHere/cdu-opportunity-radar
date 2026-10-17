"""Explicit Linux-only isolated server test. Never edits system nginx or services.

Requires an operator-created cdu-m9-isolated-test marker and a private directory.
Only fixed loopback ports are opened. All data/model proposals are synthetic.
"""
import argparse
from contextlib import closing
from datetime import timedelta
import http.client
import json
import os
import re
from pathlib import Path
import signal
import socket
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
from xml.etree import ElementTree as ET

import cdu_radar
import feedparser
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload, validate
from cdu_radar.async_queue import JobQueue
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.backup_state import backup, restore, sha256
from cdu_radar.models import Article
from cdu_radar.rss_service import make_server
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store


class NativeTrial:
    def __init__(self, root, run_name='native-test'):
        self.root = root.resolve()
        assert sys.platform == 'linux' and sys.version_info >= (3, 11)
        assert self.root.name.startswith('cdu-radar-nonprod-') and self.root.stat().st_uid == os.getuid()
        assert json.loads((self.root / 'owner.json').read_text())['kind'] == 'cdu-m9-isolated-test'
        assert Path(cdu_radar.__file__).resolve().is_relative_to(self.root / 'venv')
        assert re.fullmatch(r'native-test(?:-[a-z0-9]+)?', run_name)
        self.work = self.root / run_name; self.work.mkdir(mode=0o700, exist_ok=False)
        self.raw, self.jobs = self.work / 'raw.sqlite3', self.work / 'jobs.sqlite3'
        self.state = self.work / 'http-state'
        self.now = reference_datetime()
        self.analyzer, _ = configuration(None, True)
        self.core = self.server = self.thread = self.nginx_pid = None
        self.nginx = self.work / 'nginx'; self.nginx.mkdir(mode=0o700)
        (self.nginx / 'logs').mkdir()
        (self.nginx / 'temp').mkdir()
        self.result = {'kind': 'isolated_native_linux_server', 'python': sys.version, 'checks': [],
                       'module': str(Path(cdu_radar.__file__).resolve()), 'synthetic': True,
                       'model_calls': 0, 'official_collection_requests': 0, 'public_listeners': 0,
                       'production_reload_commands': 0}

    def check(self, name, value, **detail):
        self.result['checks'].append({'name': name, 'passed': bool(value), **detail})
        if not value:
            raise AssertionError(name)

    def request(self, path, *, tls=True, headers=None, method='GET'):
        if tls:
            conn = http.client.HTTPSConnection('127.0.0.1', 9443, timeout=10, context=self.context)
        else:
            conn = http.client.HTTPConnection('127.0.0.1', 8870, timeout=10)
        try:
            conn.request(method, path, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def pace(self):
        time.sleep(.6)

    def ready(self, path, predicate=lambda body: True):
        end = time.monotonic() + 20
        while time.monotonic() < end:
            self.pace()
            status, headers, body = self.request(path)
            if status == 200 and predicate(body):
                return headers, body
        raise AssertionError('Fresh proxy response did not become ready')

    def fixture(self):
        cases = [('合成助学金通知', '本次助学金面向在校学生，保障学生权益，申请要求请查看原文。', 'high'),
                 ('合成普通工作动态', '学院例行工作交流顺利结束，此为合成软件验收样例，没有新增学生事务。', 'low'),
                 ('合成国家级科研成果新闻', '我校国家级重要科研成果正式获批，这是具有明确来源的历史进展报道。', 'high')]
        with Store(self.raw) as store:
            store.sync_sources(load_sources())
            for index, (title, content, _) in enumerate(cases, 1):
                day = (self.now - timedelta(days=400)).date() if index == 3 else self.now.date()
                store.save_notice('computer', f'https://computer.cdu.edu.cn/info/1036/{992000+index}.htm',
                                  Article(title, day.isoformat(), content), seen_at=self.now.isoformat())
            repo = AnalysisRepository.__new__(AnalysisRepository); repo.store = store; repo.db = store.db
            with JobQueue(self.jobs, self.raw) as queue:
                queue.scan(self.analyzer)
                for index, (_, content, level) in enumerate(cases, 1):
                    notice = repo.input(index); supplied, _ = self.analyzer.prepare(notice)
                    proposal = mock_payload(supplied)
                    proposal['importance'] = {'kind': 'inference', 'level': level, 'reason': 'synthetic native check', 'quotes': [content]}
                    result = validate(proposal, notice, supplied, self.analyzer, self.now)
                    queue.db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?', (result['state'], json.dumps(result), index))

    def cli_smoke(self):
        log = (self.work / 'cli.log').open('w')
        process = subprocess.Popen([sys.executable, '-I', '-m', 'cdu_radar', 'serve-rss', '--db', str(self.raw),
                                    '--state-dir', str(self.work / 'cli-state'), '--bind', '127.0.0.1',
                                    '--port', '8766', '--collect-interval', '0'], stdout=log, stderr=log)
        try:
            end = time.monotonic() + 15
            while time.monotonic() < end:
                try:
                    conn = http.client.HTTPConnection('127.0.0.1', 8766, timeout=1)
                    conn.request('GET', '/rss/v1/all.xml'); r = conn.getresponse(); body = r.read(); conn.close()
                    if r.status == 200:
                        self.check('installed_cli_nonempty_feed', len(feedparser.parse(body).entries) == 3)
                        break
                except OSError:
                    pass
                time.sleep(.1)
            else:
                raise AssertionError('CLI failed to start')
        finally:
            process.send_signal(signal.SIGINT)
            try: process.wait(15)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(5)
            log.close()
        self.check('cli_sigint_clean_exit', process.returncode == 0)
        self.now = reference_datetime()

    def start_core(self):
        self.core = AutoFeedController(self.raw, self.state, data_mode='offline_replay', analysis_state=self.jobs,
                                       analysis_analyzer=self.analyzer, clock=lambda: self.now)
        self.server = make_server(self.core, port=8765)
        self.core.start(10)
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def stop_core(self):
        if self.server:
            self.server.shutdown(); self.server.server_close(); self.thread.join(10); self.core.close()
            self.server = None

    def start_nginx(self):
        source = self.root / 'project/deploy/m9'
        for name in ('nginx.conf.draft', 'gateway-server.inc', 'gateway-upstream.inc', 'nginx.tls.server.draft'):
            (self.nginx / name).write_text((source / name).read_text(), encoding='utf-8')
        cert, key = self.nginx / 'certificate.pem', self.nginx / 'private.key'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-sha256', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                        '-keyout', str(key), '-out', str(cert)], capture_output=True, check=True, timeout=20)
        key.chmod(0o600)
        tls = self.nginx / 'nginx.tls.server.draft'
        tls.write_text(tls.read_text().replace('/REVIEW_REQUIRED/cdu-test/fullchain.pem', str(cert))
                       .replace('/REVIEW_REQUIRED/cdu-test/privkey.pem', str(key)), encoding='utf-8')
        config = self.nginx / 'nginx.conf.draft'
        config.write_text(config.read_text().replace('# include nginx.tls.server.draft;', 'include nginx.tls.server.draft;'), encoding='utf-8')
        args = ['nginx', '-p', str(self.nginx) + '/', '-c', str(config)]
        result = subprocess.run([*args, '-t'], capture_output=True, text=True, timeout=15)
        (self.work / 'nginx-t.log').write_text(result.stdout + result.stderr)
        self.check('native_nginx_t', result.returncode == 0)
        subprocess.run(args, capture_output=True, check=True, timeout=15)
        self.nginx_pid = int((self.nginx / 'logs/nginx.pid').read_text())
        self.context = ssl.create_default_context(cafile=str(cert))

    def run(self):
        for port in (8765, 8766, 8870, 9443):
            with socket.socket() as s:
                self.check('test_port_initially_unused_' + str(port), s.connect_ex(('127.0.0.1', port)) != 0)
        self.fixture(); self.cli_smoke(); self.start_core(); self.start_nginx()
        _, original = self.ready('/rss/v1/featured.xml')
        self.original = original
        parsed = feedparser.parse(original)
        self.check('trusted_tls_rss20_selected', not parsed.bozo and len(parsed.entries) == 2)
        with socket.create_connection(('127.0.0.1', 9443), timeout=5) as raw:
            with self.context.wrap_socket(raw, server_hostname='localhost') as tls:
                self.check('verified_tls_protocol', tls.version() in {'TLSv1.2', 'TLSv1.3'}, protocol=tls.version())
        try:
            with socket.create_connection(('127.0.0.1', 9443), timeout=5) as raw:
                with ssl.create_default_context().wrap_socket(raw, server_hostname='localhost'):
                    pass
        except ssl.SSLCertVerificationError:
            self.check('untrusted_certificate_is_rejected', True)
        else:
            self.check('untrusted_certificate_is_rejected', False)
        for path in ('/', '/app.css', '/app.js', '/favicon.svg', '/api/notices', '/api/notices/v2', '/api/notices/v3',
                     '/feed.xml', '/current.xml', '/history.xml', '/rss/v1/all.xml', '/rss/v1/featured.xml', '/health'):
            self.pace(); status, h, _ = self.request(path)
            self.check('route_' + path, status == 200 and h.get('Cache-Control') == 'no-store, max-age=0')
        for path, expected in (('/approved.xml', 410), ('/.env', 404), ('/raw.sqlite3', 404), ('/jobs.sqlite3', 404),
                               ('/owner.json', 404), ('/logs/error.log', 404), ('/admin', 404),
                               ('/rss/v1/all.xml?source=invalid', 400), ('/rss/v1/all.xml?source=%FF', 400)):
            self.pace(); status, _, _ = self.request(path)
            self.check('blocked_' + path, status == expected)
        self.pace(); self.check('writes_405', self.request('/api/notices', method='POST')[0] == 405)
        self.pace(); self.check('wrong_host_403', self.request('/health', headers={'Host': 'example.invalid'})[0] == 403)
        self.pace(); status, headers, _ = self.request('/rss/v1/featured.xml')
        tag = headers['ETag']
        self.pace(); self.check('fresh_conditional_304', self.request('/rss/v1/featured.xml', headers={'If-None-Match': tag})[0] == 304)
        self.pace(); self.check('explicit_defaults_equal', self.request('/rss/v1/featured.xml?source=all&category=all')[2] == original)
        self.pace(); self.check('http_loopback_is_readable', self.request('/health', tls=False)[0] == 200)
        original_response = self.core.response_snapshot

        def delayed_response(snapshot, path):
            time.sleep(7)
            return original_response(snapshot, path)

        self.core.response_snapshot = delayed_response
        try:
            self.pace(); started = time.monotonic()
            status, timeout_headers, timeout_body = self.request('/api/notices/v3')
            self.check('nginx_upstream_timeout_is_503', status == 503 and b'"items"' not in timeout_body
                       and 'ETag' not in timeout_headers, elapsed_ms=round((time.monotonic()-started)*1000))
        finally:
            self.core.response_snapshot = original_response
        self.ready('/rss/v1/featured.xml', lambda b: b == original)
        first_guid = next(e.id for e in parsed.entries if e.link.endswith('992001.htm'))
        with closing(sqlite3.connect(self.raw)) as db:
            with db: db.execute("UPDATE notice_health SET status='withdrawn' WHERE notice_id=1")
        self.pace(); status, _, changed = self.request('/rss/v1/featured.xml', headers={'If-None-Match': tag})
        self.check('withdrawal_never_old_304', status == 503 or status == 200 and first_guid not in {e.id for e in feedparser.parse(changed).entries})
        self.ready('/rss/v1/featured.xml', lambda b: len(feedparser.parse(b).entries) == 1)
        with closing(sqlite3.connect(self.raw)) as db:
            with db: db.execute("UPDATE notice_health SET status='valid' WHERE notice_id=1")
        self.ready('/rss/v1/featured.xml', lambda b: b == original)
        with closing(sqlite3.connect(self.jobs)) as db:
            previous = db.execute('SELECT state,updated_at FROM jobs WHERE notice_id=1').fetchone()
            with db: db.execute("UPDATE jobs SET state='failed',updated_at=updated_at+1 WHERE notice_id=1")
        self.pace(); status, _, changed = self.request('/rss/v1/featured.xml', headers={'If-None-Match': tag})
        self.check('analysis_change_never_old_304', status == 503 or status == 200 and first_guid not in {e.id for e in feedparser.parse(changed).entries})
        _, partial = self.ready('/rss/v1/featured.xml', lambda b: len(feedparser.parse(b).entries) == 1)
        self.check('partial_coverage_explicit', ET.fromstring(partial).findtext('./channel/{urn:cdu-radar:rss:v1}analysis_state') == 'degraded')
        with closing(sqlite3.connect(self.jobs)) as db:
            with db: db.execute('UPDATE jobs SET state=?,updated_at=? WHERE notice_id=1', previous)
        self.ready('/rss/v1/featured.xml', lambda b: b == original)
        point = self.now; self.now = point + timedelta(hours=73); self.core.wakeup.set()
        self.ready('/rss/v1/all.xml', lambda b: not feedparser.parse(b).entries)
        self.check('expired_sources_no_items', True)
        self.now = point; self.core.wakeup.set(); self.ready('/rss/v1/featured.xml', lambda b: b == original)
        with socket.create_connection(('127.0.0.1', 8870), timeout=8) as slow:
            slow.sendall(b'GET / HTTP/1.1\r\nHost: localhost:8870\r\n')
            start = time.monotonic(); data = slow.recv(4096)
            elapsed = time.monotonic() - start
            self.check('slow_header_bounded_close', 4.5 <= elapsed <= 7.5 and
                       (not data or b'408' in data.split(b'\r\n', 1)[0]),
                       elapsed_ms=round(elapsed*1000), response='eof' if not data else data.split(b'\r\n', 1)[0].decode('ascii', errors='replace'))
        counts = {}
        for _ in range(50):
            status, h, _ = self.request('/health')
            counts[status] = counts.get(status, 0) + 1
            if status == 429:
                assert h.get('Retry-After') == '1'
        self.check('gateway_rate_limit_429', counts.get(429, 0) > 0 and set(counts) <= {200, 429}, statuses=counts)
        self.stop_core()
        time.sleep(12)
        self.check('upstream_stop_is_503', self.request('/rss/v1/all.xml')[0] == 503)
        before = {str(p): sha256(p) for p in (self.raw, self.jobs, self.state/'subscriptions.sqlite3')}
        backup(self.raw, self.state, self.work/'bundle', analysis_db=self.jobs, quiesced=True)
        restored = restore(self.work/'bundle', self.work/'restored')
        self.check('backup_sources_unchanged', before == {p: sha256(Path(p)) for p in before})
        self.raw, self.jobs, self.state = Path(restored['raw_db']), Path(restored['analysis_db']), Path(restored['state_dir'])
        self.start_core(); _, recovered = self.ready('/rss/v1/featured.xml')
        self.check('native_restore_preserves_feed', recovered == original)
        self.stop_core()
        self.raw, self.jobs, self.state = self.work/'raw.sqlite3', self.work/'jobs.sqlite3', self.work/'http-state'
        self.start_core(); _, reverted = self.ready('/rss/v1/featured.xml')
        self.check('isolated_data_path_rollback', reverted == original)
        self.save()

    def save(self):
        (self.work/'result.json').write_text(json.dumps(self.result, ensure_ascii=False, indent=2), encoding='utf-8')

    def close(self):
        if self.nginx_pid:
            command = Path(f'/proc/{self.nginx_pid}/cmdline').read_bytes().decode(errors='replace')
            assert str(self.nginx) in command
            os.kill(self.nginx_pid, signal.SIGQUIT)
        self.stop_core()
        end = time.monotonic() + 10
        while time.monotonic() < end:
            values = []
            for port in (8765, 8766, 8870, 9443):
                with socket.socket() as sock: values.append(sock.connect_ex(('127.0.0.1', port)) != 0)
            if all(values): break
            time.sleep(.1)
        self.check('all_test_ports_closed', all(values))
        self.result['passed'] = not self.result.get('run_error') and all(c['passed'] for c in self.result['checks'])
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--run-name', default='native-test', help='NEW attempt directory; prior evidence is never overwritten')
    parser.add_argument('--hold', action='store_true', help='Keep test instances for a client over an SSH loopback tunnel')
    args = parser.parse_args(); trial = NativeTrial(args.root, args.run_name)
    try:
        trial.run()
        print(json.dumps({'phase': 'ready', 'checks': len(trial.result['checks']), 'hold': args.hold}), flush=True)
        if args.hold:
            while not (trial.root/'stop-native').exists(): time.sleep(.2)
    except BaseException as exc:
        trial.result['run_error'] = type(exc).__name__
        raise
    finally:
        trial.close()


if __name__ == '__main__':
    main()
