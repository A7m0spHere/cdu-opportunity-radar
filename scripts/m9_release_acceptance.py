"""Linux-only A -> B -> A -> B wheel switch on NEW synthetic restored state.

No system services, public listeners, model requests or production data are used.
Run within the same explicit resource limits as the M9 native acceptance.
"""
import argparse
from contextlib import closing
import hashlib
import http.client
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import time

import cdu_radar
import feedparser
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.async_cli import configuration
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.backup_state import backup, restore
from cdu_radar.rss_service import make_server

PORT = 8767
DISCLAIMER = '非成都大学官方服务'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def closed():
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', PORT)) != 0


def worker(args):
    data = args.root / 'release-test' / 'shared'
    analyzer, _ = configuration(None, True)
    core = AutoFeedController(data / 'raw.sqlite3', data / 'http-state', data_mode='offline_replay',
                              analysis_state=data / 'jobs.sqlite3', analysis_analyzer=analyzer,
                              clock=lambda: reference_datetime(args.as_of))
    server = make_server(core, port=PORT)
    print(json.dumps({'module': str(Path(cdu_radar.__file__).resolve())}), flush=True)
    core.start(10)
    try:
        server.serve_forever(poll_interval=.05)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        core.close()


def request(path, headers=None):
    conn = http.client.HTTPConnection('127.0.0.1', PORT, timeout=3)
    try:
        conn.request('GET', path, headers=headers or {})
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


def parent(args):
    root = args.root
    if sys.platform != 'linux' or not root.name.startswith('cdu-radar-nonprod-'):
        raise ValueError('Explicit isolated Linux root required')
    if root.stat().st_uid != os.getuid() or json.loads((root/'owner.json').read_text())['kind'] != 'cdu-m9-isolated-test':
        raise ValueError('Wrong owner or test marker')
    for python in (args.baseline_python, args.candidate_python):
        if not python.absolute().is_relative_to(root) or not python.is_file():
            raise ValueError('Private installed interpreters required')
    if not closed():
        raise ValueError('Test port in use')
    work = root / 'release-test'
    work.mkdir(mode=0o700, exist_ok=False)
    restored = restore(root/'native-test-v2/bundle', work/'shared')
    data = work/'shared'
    result = {'kind': 'isolated_native_wheel_upgrade_rollback', 'synthetic': True,
              'model_calls': 0, 'official_collection_requests': 0, 'phases': [], 'checks': []}
    process = None

    def check(name, value):
        result['checks'].append({'name': name, 'passed': bool(value)})
        if not value:
            raise AssertionError(name)

    def identities():
        with closing(sqlite3.connect(data/'http-state/subscriptions.sqlite3')) as db:
            return db.execute('SELECT * FROM first_featured ORDER BY 1').fetchall()

    try:
        point = reference_datetime().isoformat()
        original = ids = protected = None
        for name, python, expect_notice in [('A_baseline', args.baseline_python, False),
                                             ('B_upgrade', args.candidate_python, True),
                                             ('A_rollback', args.baseline_python, False),
                                             ('B_reapply', args.candidate_python, True)]:
            with (work/(name+'.log')).open('x') as log:
                process = subprocess.Popen([str(python), '-I', str(Path(__file__).resolve()), '--root', str(root),
                                            '--worker', '--as-of', point], stdout=log, stderr=log)
                try:
                    deadline = time.monotonic()+25
                    while time.monotonic()<deadline:
                        try:
                            status, headers, body = request('/rss/v1/featured.xml')
                            if status == 200:
                                break
                        except OSError:
                            pass
                        if process.poll() is not None:
                            raise RuntimeError('Isolated worker exited before readiness')
                        time.sleep(.1)
                    else:
                        raise TimeoutError('Isolated worker readiness')
                    entries = feedparser.parse(body).entries
                    check(name+'_two_featured', len(entries)==2)
                    check(name+'_conditional_304', request('/rss/v1/featured.xml', {'If-None-Match':headers['ETag']})[0]==304)
                    check(name+'_disclaimer_version', (DISCLAIMER in request('/')[2].decode('utf-8')) == expect_notice)
                    if original is None:
                        original = body
                    check(name+'_feed_bytes_unchanged', body == original)
                finally:
                    process.send_signal(signal.SIGINT)
                    try:
                        code = process.wait(15)
                    except subprocess.TimeoutExpired:
                        process.kill(); process.wait(5); raise
                    process = None
                check(name+'_clean_sigint', code==0)
                check(name+'_port_closed', closed())
            module=json.loads((work/(name+'.log')).read_text().splitlines()[0])['module']
            check(name+'_installed_module', Path(module).is_relative_to(python.absolute().parent.parent/'lib'))
            current = {str(path.name):sha(path) for path in (data/'raw.sqlite3', data/'jobs.sqlite3')}
            if protected is None:
                protected, ids = current, identities()
                backup(data/'raw.sqlite3', data/'http-state', work/'before-upgrade', analysis_db=data/'jobs.sqlite3', quiesced=True)
            check(name+'_raw_and_ledger_unchanged', current == protected)
            check(name+'_first_featured_unchanged', identities() == ids)
            result['phases'].append({'name': name, 'interpreter': str(python), 'module': module,
                                     'feed_sha256': hashlib.sha256(original).hexdigest(), 'entries':len(entries)})
        check('final_test_port_closed', closed())
        result['passed'] = True
    except BaseException as exc:
        result.update(passed=False, error=type(exc).__name__)
        raise
    finally:
        if process is not None and process.poll() is None:
            process.kill(); process.wait(5)
        (work/'result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':result['passed'],'checks':len(result['checks']),'phases':len(result['phases'])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=lambda p:Path(p).resolve(),required=True)
    parser.add_argument('--baseline-python',type=Path)
    parser.add_argument('--candidate-python',type=Path)
    parser.add_argument('--worker',action='store_true')
    parser.add_argument('--as-of')
    args=parser.parse_args()
    if args.worker:
        worker(args)
    elif args.baseline_python and args.candidate_python:
        parent(args)
    else:
        parser.error('Both private installed interpreters required')


if __name__ == '__main__':
    main()
