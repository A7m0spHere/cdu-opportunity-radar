"""Issue #6 desktop-only acceptance against an installed package and isolated DB.

Only literal loopback sockets are used. The private mode file controls this test
runner, never production HTTP. Stored originals are replayed at an explicit time;
their current Internet reachability and a second device are separate checks.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import http.client
import ipaddress
import json
from pathlib import Path
import platform
import socket
import sqlite3
import statistics
import sys
import threading
import time

import cdu_radar
import feedparser
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.lan_preview import make_preview_server
from cdu_radar.rss_service import make_server


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def protected_rows(path):
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True) as db:
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        values = {name: db.execute('SELECT * FROM ' + name + ' ORDER BY rowid').fetchall()
                  for name in ('notices', 'notice_sources', 'notice_links', 'notice_health',
                               'manual_labels', 'analysis_reviews', 'publication_reviews',
                               'publication_state', 'publication_events', 'notice_analyses') if name in names}
    return hashlib.sha256(json.dumps(values, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class Trial:
    def __init__(self, directory, source, as_of):
        self.root = directory.resolve()
        self.root.mkdir(parents=True, exist_ok=False)
        self.source, self.point = source.resolve(), reference_datetime(as_of)
        self.source_hash = digest(self.source)
        self.raw = self.root / 'replay.sqlite3'
        self.before = protected_rows(self.source)
        with sqlite3.connect(self.source.as_uri() + '?mode=ro', uri=True) as original:
            with sqlite3.connect(self.raw) as copy:
                original.backup(copy)
        self.mode = 'normal'
        self.core = self.server = self.thread = None
        self.proxy = self.proxy_thread = None
        self.core_port = 0
        self.ports = []
        self.report = {'kind': 'm5c-desktop-only', 'reference_time': self.point.isoformat(),
                       'platform': platform.platform(), 'python': platform.python_version(),
                       'module_path': str(Path(cdu_radar.__file__).resolve()),
                       'source_sha256': self.source_hash, 'checks': [],
                       'external_requests_by_runner': 0, 'model_calls': 0, 'lan_listeners': 0,
                       'human_participants': 0, 'second_device': 'not_executed',
                       'linux_native': 'not_executed'}

    def check(self, name, condition, **details):
        self.report['checks'].append({'name': name, 'passed': bool(condition), **details})
        if not condition:
            raise AssertionError(name)

    def start_core(self):
        self.core = AutoFeedController(self.raw, self.root / 'http-state', data_mode='offline_replay',
                                       clock=lambda: self.point)
        original = self.core.response_snapshot

        def fault(snapshot, path):
            if self.mode == 'timeout':
                time.sleep(4)
            return original(snapshot, path)

        self.core.response_snapshot = fault
        self.server = make_server(self.core, port=self.core_port)
        self.core_port = self.server.server_address[1]
        self.ports.append(self.core_port)
        self.core.start(10)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def stop_core(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(6)
            self.core.close()
            self.server = None

    def request(self, path, *, port=None, method='GET', headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', port or self.proxy.server_address[1], timeout=6)
        try:
            connection.request(method, path, headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read(2 * 1024 * 1024 + 1)
        finally:
            connection.close()

    def wait_ready(self):
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            status, _, body = self.request('/api/notices/v3')
            if status == 200:
                return json.loads(body)
            time.sleep(.1)
        raise AssertionError('Desktop preview did not recover')

    def start(self):
        self.start_core()
        self.proxy = make_preview_server(self.core_port)
        self.ports.append(self.proxy.server_address[1])
        self.proxy_thread = threading.Thread(target=self.proxy.serve_forever,
                                             kwargs={'poll_interval': .05}, daemon=True)
        self.proxy_thread.start()
        self.write_state()

    def write_state(self):
        value = {'mode': self.mode, 'port': self.proxy.server_address[1], 'core_port': self.core_port,
                 'url': f'http://127.0.0.1:{self.proxy.server_address[1]}/', 'loopback_only': True}
        (self.root / 'preview.json').write_text(json.dumps(value), encoding='utf-8')

    def automated(self):
        payload = self.wait_ready()
        items = payload['items']
        self.check('nonempty_real_stored_replay', bool(items), count=len(items), counts=payload['counts'])
        self.check('unknown_registration_preserved', any(i['registration_status'] == 'unknown' for i in items))
        self.check('historical_items_available', any(i['listing'] == 'history' for i in items))
        feed = feedparser.parse(self.request('/feed.xml')[2])
        self.check('rss20_and_api_guid_equality', not feed.bozo and {e.id for e in feed.entries} == {i['guid'] for i in items})
        status, h, _ = self.request('/feed.xml')
        self.check('validated_etag_304', status == 200 and self.request('/feed.xml', headers={'If-None-Match': h['ETag']})[0] == 304)
        self.check('head_without_body', self.request('/api/notices/v3', method='HEAD')[2] == b'')
        self.check('no_store_without_credentials', h['Cache-Control'] == 'no-store, max-age=0' and 'Set-Cookie' not in h)
        private_paths = ['/replay.sqlite3', '/subscriptions.sqlite3', '/.env', '/owner.json',
                         '/publisher.lock', '/admin', '/logs', '/../replay.sqlite3', '/%2e%2e/replay.sqlite3']
        self.check('private_and_admin_paths_rejected', all(self.request(p)[0] == 404 for p in private_paths), paths=len(private_paths))
        self.check('read_only_methods', all(self.request('/api/notices', method=m)[0] == 405
                                          for m in ('POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS')))
        self.check('invalid_filter_400', self.request('/rss/v1/all.xml?source=invalid')[0] == 400)
        self.check('approved_feed_retired', self.request('/approved.xml')[0] == 410)
        self.check('host_and_cross_site_rejected', self.request('/api/notices', headers={'Host': 'example.invalid'})[0] == 403
                   and self.request('/api/notices', headers={'Origin': 'https://example.invalid'})[0] == 403)
        denied = make_preview_server(self.core_port, clients=('127.0.0.2',))
        thread = threading.Thread(target=denied.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        thread.start()
        self.ports.append(denied.server_address[1])
        try:
            self.check('actual_loopback_peer_not_allowlisted_403', self.request('/api/notices', port=denied.server_address[1])[0] == 403)
            self.check('forwarded_ip_cannot_bypass_acl', self.request('/api/notices', port=denied.server_address[1],
                       headers={'X-Forwarded-For': '127.0.0.2'})[0] == 403)
        finally:
            denied.shutdown(); denied.server_close(); thread.join(6)
        samples = []

        def reads(_):
            values = []
            for _ in range(10):
                start = time.perf_counter()
                status, _, body = self.request('/api/notices/v3')
                assert status in {200, 503}
                if status == 200:
                    assert len(json.loads(body)['items']) == len(items)
                else:
                    assert b'"items"' not in body
                values.append({'status': status, 'ms': (time.perf_counter() - start) * 1000})
            return values

        with ThreadPoolExecutor(max_workers=2) as pool:
            for values in pool.map(reads, range(2)):
                samples.extend(values)
        successes = sum(s['status'] == 200 for s in samples)
        self.check('two_local_clients_bounded_smoke', len(samples) == 20 and successes > 0,
                   requests=20, status_200=successes, status_503=20-successes,
                   median_ms=round(statistics.median(s['ms'] for s in samples), 2),
                   max_ms=round(max(s['ms'] for s in samples), 2), publisher_interval_seconds=10,
                   samples=[{'status': s['status'], 'ms': round(s['ms'], 2)} for s in samples])
        self.check('local_contention_recovers', len(self.wait_ready()['items']) == len(items))
        self.mode = 'timeout'
        start = time.monotonic()
        status, headers, body = self.request('/api/notices/v3', headers={'If-None-Match': '*'})
        self.check('upstream_timeout_503_without_old_body', status == 503 and b'"items"' not in body and 'ETag' not in headers,
                   elapsed_ms=round((time.monotonic() - start) * 1000))
        self.mode = 'normal'
        self.wait_ready()
        self.stop_core()
        self.check('upstream_stopped_503_without_old_body', self.request('/api/notices/v3')[0] == 503)
        self.start_core()
        self.check('upstream_restart_recovers', len(self.wait_ready()['items']) == len(items))
        self.check('protected_replay_rows_unchanged', protected_rows(self.raw) == self.before)
        self.save()

    def set_mode(self, mode):
        if mode == self.mode:
            return
        previous = self.mode
        if previous == 'upstream_stopped':
            self.start_core()
        self.mode = mode
        if mode == 'upstream_stopped':
            self.stop_core()
        elif mode == 'source_unavailable':
            self.core.db = self.root / 'intentionally-missing.sqlite3'
            self.core.wakeup.set()
        elif mode in {'normal', 'timeout'}:
            self.core.db = self.raw
            self.core.wakeup.set()
        else:
            raise ValueError('Unsupported private test mode')
        self.write_state()

    def save(self):
        (self.root / 'acceptance.json').write_text(json.dumps(self.report, ensure_ascii=False, indent=2), encoding='utf-8')

    def close(self):
        if self.proxy:
            self.proxy.shutdown(); self.proxy.server_close(); self.proxy_thread.join(6)
        self.stop_core()
        self.check('source_database_hash_unchanged', digest(self.source) == self.source_hash)
        closed = []
        for port in set(self.ports):
            with socket.socket() as probe:
                probe.settimeout(.5)
                closed.append(probe.connect_ex(('127.0.0.1', port)) != 0)
        self.check('all_owned_ports_closed', all(closed), count=len(closed))
        self.report['services_stopped'] = True
        self.report['passed'] = not self.report.get('run_error') and all(c['passed'] for c in self.report['checks'])
        self.save()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--source-db', type=Path, required=True)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--serve', action='store_true')
    args = parser.parse_args()
    # Any accidental outbound requests by the runner are test failures.
    original_connect = socket.socket.connect

    def loopback_connect(sock, address):
        if not ipaddress.ip_address(address[0]).is_loopback:
            raise AssertionError('Only literal loopback traffic is allowed')
        return original_connect(sock, address)

    socket.socket.connect = loopback_connect
    trial = Trial(args.directory, args.source_db, args.as_of)
    try:
        trial.start()
        trial.automated()
        print(json.dumps({'checks_passed': len(trial.report['checks']), 'port': trial.proxy.server_address[1],
                          'mode': 'desktop_only_real_stored_replay'}), flush=True)
        if args.serve:
            while not (trial.root / 'stop').exists():
                mode = (trial.root / 'mode').read_text(encoding='utf-8').strip() if (trial.root / 'mode').exists() else 'normal'
                trial.set_mode(mode)
                time.sleep(.1)
    except KeyboardInterrupt:
        pass
    except BaseException as exc:
        trial.report['run_error'] = type(exc).__name__
        raise
    finally:
        trial.close()


if __name__ == '__main__':
    main()
