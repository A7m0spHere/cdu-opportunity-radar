"""One read-only local snapshot; never schedules collection or model work."""
import argparse
from collections import Counter
from contextlib import contextmanager
import ctypes
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.analysis_models import reference_datetime

ROUTES = {
    'health': '/health',
    'json_all': '/api/notices/v4?audience=all',
    'json_featured': '/api/notices/v4?selection=featured&audience=all',
    'rss_all': '/rss/v2/all.xml?audience=all',
    'rss_featured': '/rss/v2/featured.xml?audience=all',
}


@contextmanager
def reader(path):
    path = Path(path).resolve()
    db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.2)
    try:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        yield db
    finally:
        db.close()


def local_base(value):
    parts = urlsplit(value)
    if (parts.scheme != 'http' or parts.hostname != '127.0.0.1'
            or not parts.port or parts.username or parts.password
            or parts.path not in {'', '/'} or parts.query or parts.fragment):
        raise ValueError('Only a literal http://127.0.0.1:PORT base URL is allowed')
    return value.rstrip('/')


def memory_bytes():
    if os.name == 'nt':
        from ctypes import wintypes
        class Counters(ctypes.Structure):
            _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
                (name, ctypes.c_size_t) for name in (
                    'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                    'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                    'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
        value = Counters(); value.cb = ctypes.sizeof(value)
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        psapi = ctypes.WinDLL('psapi', use_last_error=True)
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(value), value.cb):
            return value.WorkingSetSize
        return None
    try:
        return int(Path('/proc/self/statm').read_text().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        return None


def database_snapshot(raw, now):
    with reader(raw) as db:
        tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        counts = {table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0]
                  for table in ('notices', 'request_events', 'collection_runs', 'auto_publications') if table in tables}
        health = [dict(row) for row in db.execute('SELECT status,checked_at FROM notice_health')]
        ages = [max(0, (now-reference_datetime(row['checked_at'])).total_seconds()) for row in health]
        checks = [dict(row) for row in db.execute('SELECT state,next_check_at,failures,last_error FROM notice_rechecks')]
        due = [row for row in checks if reference_datetime(row['next_check_at']) <= now]
        return dict(status='ok', counts=counts, health_states=dict(Counter(row['status'] for row in health)),
                    maximum_health_age_seconds=max(ages, default=None),
                    health_older_than_72h=sum(age > 72*3600 for age in ages),
                    rechecks=dict(states=dict(Counter(row['state'] for row in checks)), due=len(due),
                                  oldest_due_seconds=max(((now-reference_datetime(row['next_check_at'])).total_seconds()
                                                         for row in due), default=0),
                                  failures=sum(row['failures'] for row in checks)))


def sidecar_snapshot(path, kind):
    if path is None:
        return {'status': 'not_configured'}
    if not Path(path).is_file():
        return {'status': 'missing'}
    try:
        with reader(path) as db:
            if kind == 'analysis':
                return dict(status='ok', states=dict(db.execute('SELECT state,count(*) FROM jobs GROUP BY state')),
                            attempts=db.execute('SELECT count(*) FROM attempts').fetchone()[0],
                            billable_reservations=db.execute('SELECT count(*) FROM attempts WHERE billable=1').fetchone()[0])
            tables = ('consumer_subscriptions', 'consumer_items', 'consumer_runs') if kind == 'consumer' else ('first_featured', 'collection')
            return dict(status='ok', counts={table: db.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in tables})
    except sqlite3.Error as exc:
        return {'status': 'unavailable', 'error': type(exc).__name__}


def http_snapshot(base):
    results = {}
    with httpx.Client(timeout=5, trust_env=False, follow_redirects=False) as client:
        for name, route in ROUTES.items():
            try:
                with client.stream('GET', local_base(base)+route) as response:
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > 2*1024*1024:
                            raise ValueError('Probe response exceeds 2 MiB')
                    result = {'status': response.status_code, 'bytes': len(body)}
                    if response.status_code == 200:
                        if name.startswith('json'):
                            payload = json.loads(body)
                            result.update(guids=sorted(item['guid'] for item in payload['items']),
                                          featured=payload['featured'])
                        elif name.startswith('rss'):
                            if b'<!DOCTYPE' in body.upper() or b'<!ENTITY' in body.upper():
                                raise ValueError('DTD is not accepted')
                            channel = ET.fromstring(body).find('channel')
                            result.update(guids=sorted(item.findtext('guid') for item in channel.findall('item')),
                                          identities=sorted((item.findtext('guid'), item.findtext('pubDate'))
                                                            for item in channel.findall('item')),
                                          truncated=channel.findtext('{urn:cdu-radar:rss:v1}truncated'))
                    results[name] = result
            except (httpx.HTTPError, ValueError, KeyError, TypeError, ET.ParseError) as exc:
                results[name] = {'status': 'error', 'error': type(exc).__name__}
    return results


def snapshot(raw, state_dir, *, analysis=None, consumer=None, base=None, now=None):
    now = reference_datetime(now)
    try:
        raw_result = database_snapshot(raw, now)
    except (sqlite3.Error, ValueError) as exc:
        raw_result = {'status': 'unavailable', 'error': type(exc).__name__}
    wire = http_snapshot(base) if base else {}
    consistency = {}
    for selection in ('all', 'featured'):
        api, rss = wire.get('json_'+selection, {}), wire.get('rss_'+selection, {})
        comparable = api.get('status') == rss.get('status') == 200 and rss.get('truncated') == 'false'
        consistency[selection] = (api['guids'] == rss['guids']) if comparable else None
    return dict(schema_version=1, observed_at=now.isoformat(), scope='one_shot_local_read_only',
                raw=raw_result, analysis=sidecar_snapshot(analysis, 'analysis'),
                subscriptions=sidecar_snapshot(Path(state_dir)/'subscriptions.sqlite3', 'subscription'),
                consumer=sidecar_snapshot(consumer, 'consumer'), http=wire, consistency=consistency,
                resources=dict(probe_pid=os.getpid(), probe_working_set_bytes=memory_bytes(),
                               disk_free_bytes=shutil.disk_usage(Path(raw).resolve().parent).free),
                collection_triggered=False, model_requests=0)


def write_new(path, value):
    with Path(path).open('x', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-db', type=Path, required=True)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--analysis-db', type=Path)
    parser.add_argument('--consumer-db', type=Path)
    parser.add_argument('--base-url', type=local_base)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    write_new(args.output, snapshot(args.raw_db, args.state_dir, analysis=args.analysis_db,
                                   consumer=args.consumer_db, base=args.base_url))
