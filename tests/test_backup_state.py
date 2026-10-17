"""Offline preparation only: temporary databases, no proxy deployment or API."""
from pathlib import Path
import json
import shutil
import sqlite3
import subprocess
import sys

import pytest

from cdu_radar.backup_state import backup, restore, sha256
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.async_queue import JobQueue
from test_auto_publish import auto_service, NOW
from test_featured import fill, raw_path
from test_rss_service import loopback_only


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()


def stopped_bundle(service, tmp_path):
    store, c, *_ = service
    analyzer, jobs, _ = fill(store, tmp_path)
    c.data_mode = 'offline_replay'; c.analysis_analyzer = analyzer; c.analysis_state = jobs
    with JobQueue(jobs, raw_path(store)) as queue:
        ident = queue.db.execute('SELECT id FROM jobs LIMIT 1').fetchone()[0]
        queue.db.execute('INSERT INTO attempts (id,job_id,ordinal,reserved_at,reserved_tokens,billable,dispatch_started,state) VALUES (?,?,?,?,?,?,?,?)',
                         ('synthetic-backup-reservation', ident, 1, 1, 1234, 1, 1, 'unknown'))
    assert c.refresh()
    with c.response_gate(c.snapshot, '/rss/v1/featured.xml') as response:
        original = response.body
    raw = raw_path(store)
    first = rows(c.subscription_path, 'first_featured')
    c.close()
    hashes = {str(p): sha256(p) for p in (raw, jobs, c.subscription_path)}
    directory = tmp_path / 'bundle'
    backup(raw, c.lease.root, directory, analysis_db=jobs, quiesced=True, revision='offline-test')
    assert hashes == {p: sha256(Path(p)) for p in hashes}
    return directory, analyzer, jobs, original, first, raw


def test_full_restore_preserves_originals_budget_and_first_times(auto_service, tmp_path):
    bundle, analyzer, jobs, original, first, raw = stopped_bundle(auto_service, tmp_path)
    restored = restore(bundle, tmp_path / 'recovered')
    raw_copy, jobs_copy = Path(restored['raw_db']), Path(restored['analysis_db'])
    state_copy = Path(restored['state_dir'])
    assert sha256(raw_copy) == sha256(bundle / 'raw.sqlite3')
    for table in ('notices', 'notice_sources', 'notice_links', 'notice_health', 'auto_publications'):
        assert rows(raw_copy, table) == rows(raw, table)
    for table in ('jobs', 'attempts', 'input_failures'):
        assert rows(jobs_copy, table) == rows(jobs, table)
    assert rows(state_copy / 'subscriptions.sqlite3', 'first_featured') == first
    assert rows(state_copy / 'subscriptions.sqlite3', 'collection') == []
    with JobQueue(jobs_copy, raw_copy, read_only=True) as queue:
        queue.ensure_source()
    # Revalidation uses existing validated proposals; no listener is opened.
    controller = AutoFeedController(raw_copy, state_copy, data_mode='offline_replay',
                                   analysis_state=jobs_copy, analysis_analyzer=analyzer, clock=lambda: NOW)
    try:
        assert controller.snapshot is None
        assert controller.refresh()
        with controller.response_gate(controller.snapshot, '/rss/v1/featured.xml') as response:
            assert response.body == original
    finally:
        controller.close()
    assert restored['services_started'] is False


def test_raw_only_restore_keeps_human_history_and_health(auto_service, tmp_path):
    store, c, *_ = auto_service
    with store.db:
        store.db.execute('CREATE TABLE synthetic_human_history(id INTEGER PRIMARY KEY,note TEXT)')
        store.db.execute("INSERT INTO synthetic_human_history VALUES (1,'unchanged synthetic human record')")
        store.db.execute("UPDATE notice_health SET status='withdrawn' WHERE notice_id=1")
    raw = raw_path(store)
    c.close()
    directory = tmp_path / 'raw-bundle'
    backup(raw, c.lease.root, directory, quiesced=True)
    result = restore(directory, tmp_path / 'raw-restored')
    assert result['analysis_db'] is None
    assert rows(Path(result['raw_db']), 'synthetic_human_history') == rows(raw, 'synthetic_human_history')
    assert rows(Path(result['raw_db']), 'notice_health') == rows(raw, 'notice_health')


def test_backup_requires_stopped_writers_and_explicit_confirmation(auto_service, tmp_path):
    store, c, *_ = auto_service
    with pytest.raises(ValueError):
        backup(raw_path(store), c.lease.root, tmp_path / 'never', quiesced=False)
    with pytest.raises(OSError):
        backup(raw_path(store), c.lease.root, tmp_path / 'active', quiesced=True)
    assert not (tmp_path / 'never').exists() and not (tmp_path / 'active').exists()


@pytest.mark.parametrize('fault', ['checksum', 'missing', 'traversal', 'duplicate', 'extra', 'no_manifest', 'queue_identity', 'queue_extra_table', 'schema'])
def test_invalid_bundle_is_refused_before_any_restore_write(auto_service, tmp_path, fault):
    bundle, *_ = stopped_bundle(auto_service, tmp_path)
    manifest_path = bundle / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if fault == 'checksum':
        with (bundle / 'raw.sqlite3').open('ab') as stream:
            stream.write(b'corruption')
    elif fault == 'missing':
        (bundle / 'jobs.sqlite3').unlink()
    elif fault == 'traversal':
        manifest['files'][0]['name'] = '../outside.sqlite3'
    elif fault == 'duplicate':
        manifest['files'][1] = manifest['files'][0]
    elif fault == 'extra':
        (bundle / '.env').write_text('synthetic-private-file', encoding='utf-8')
    elif fault == 'no_manifest':
        manifest_path.unlink()
    else:
        name = 'jobs.sqlite3' if fault in {'queue_identity', 'queue_extra_table'} else 'raw.sqlite3'
        with sqlite3.connect(bundle / name) as db:
            if fault == 'queue_identity':
                db.execute("UPDATE metadata SET value='wrong-source' WHERE key='raw_identity'")
            elif fault == 'queue_extra_table':
                db.execute('CREATE TABLE unrelated_private_data(value TEXT)')
            else:
                db.execute('DROP TABLE auto_publications')
        entry = next(e for e in manifest['files'] if e['name'] == name)
        entry.update(bytes=(bundle / name).stat().st_size, sha256=sha256(bundle / name))
    if fault != 'no_manifest':
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
    output = tmp_path / 'refused'
    with pytest.raises((ValueError, sqlite3.Error)):
        restore(bundle, output)
    assert not output.exists()


def test_existing_output_and_wal_are_refused(auto_service, tmp_path):
    store, c, *_ = auto_service
    raw = raw_path(store)
    c.close()
    directory = tmp_path / 'existing'
    directory.mkdir(); sentinel = directory / 'keep.txt'; sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(ValueError):
        backup(raw, c.lease.root, directory, quiesced=True)
    assert sentinel.read_text(encoding='utf-8') == 'keep'
    store.db.execute('PRAGMA journal_mode=WAL')
    with pytest.raises(ValueError):
        backup(raw, c.lease.root, tmp_path / 'wal', quiesced=True)
    assert not (tmp_path / 'wal').exists()


def test_restore_refuses_existing_directory_without_touching_data(auto_service, tmp_path):
    bundle, *_ = stopped_bundle(auto_service, tmp_path)
    existing = tmp_path / 'existing'
    existing.mkdir(); sentinel = existing / 'important.txt'; sentinel.write_text('keep', encoding='utf-8')
    with pytest.raises(ValueError):
        restore(bundle, existing)
    assert sentinel.read_text(encoding='utf-8') == 'keep'
    with pytest.raises(ValueError):
        restore(bundle, bundle / 'nested')
    assert not (bundle / 'nested').exists()


def test_draft_policy_and_real_public_routes():
    from scripts.validate_m9_design import validate
    result = validate(Path(__file__).resolve().parents[1] / 'deploy/m9')
    assert result['passed'] and not result['nginx_syntax_checked'] and not result['deployment_performed']


@pytest.mark.parametrize('file,old,new', [
    ('nginx.conf.draft', '127.0.0.1:8870', '0.0.0.0:8870'),
    ('nginx.tls.server.draft', '127.0.0.1:9443', '0.0.0.0:9443'),
    ('gateway-server.inc', 'location / { return 404; }', 'location / { root /private; }'),
    ('gateway-server.inc', 'return 405;', 'return 200;'),
    ('gateway-upstream.inc', 'proxy_cache off;', 'proxy_cache cache_zone;'),
    ('gateway-upstream.inc', 'proxy_pass_request_headers off;', 'proxy_pass_request_headers on;'),
    ('gateway-upstream.inc', 'proxy_set_header Connection "";', 'proxy_set_header Authorization $http_authorization;'),
    ('gateway-upstream.inc', 'http://127.0.0.1:8765', 'http://example.invalid'),
    ('gateway-server.inc', 'include gateway-upstream.inc;', 'include /private/config.inc;'),
    ('cdu-radar.service.draft', '--collect-interval 0', '--collect-interval 3600'),
])
def test_deployment_policy_mutations_fail(tmp_path, file, old, new):
    from scripts.validate_m9_design import validate
    source = Path(__file__).resolve().parents[1] / 'deploy/m9'
    copied = tmp_path / 'draft'
    shutil.copytree(source, copied)
    path = copied / file
    path.write_text(path.read_text(encoding='utf-8').replace(old, new), encoding='utf-8')
    assert not validate(copied)['passed']


def test_backup_cli_does_not_start_services_or_create_missing_sources(tmp_path):
    root = Path(__file__).resolve().parents[1]
    output = tmp_path / 'refused'
    result = subprocess.run([sys.executable, str(root / 'scripts/backup_state.py'), 'backup',
                             '--raw-db', str(tmp_path / 'missing.sqlite3'), '--state-dir', str(tmp_path / 'state'),
                             '--output', str(output)], capture_output=True, timeout=20)
    assert result.returncode == 2 and not output.exists() and not (tmp_path / 'missing.sqlite3').exists()
