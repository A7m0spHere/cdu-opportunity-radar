"""Protect the probe's read-only boundary and meaningful availability semantics."""
from datetime import timedelta
import hashlib
import sqlite3

import pytest

from cdu_radar.analysis_models import reference_datetime
from cdu_radar.models import Article
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store
from scripts.m10_probe import database_snapshot, local_base, snapshot, write_new
from scripts.m10_local_acceptance import live, logical_digests, recover_live
from test_rss_service import loopback_only


@pytest.mark.parametrize('value', [
    'http://localhost:8765', 'https://127.0.0.1:8765', 'http://127.0.0.1',
    'http://127.0.0.1:8765/private', 'http://user:password@127.0.0.1:8765',
    'http://127.0.0.1:8765?host=example.org', 'http://example.org:8765',
])
def test_probe_cannot_target_remote_or_arbitrary_paths(value):
    with pytest.raises(ValueError):
        local_base(value)


def test_read_only_health_and_due_backlog_are_measured_without_renewing(tmp_path):
    raw = tmp_path/'notices.sqlite3'
    now = reference_datetime('2026-10-22T12:00:00+08:00')
    with Store(raw) as store:
        store.sync_sources(load_sources())
        store.save_notice('computer', 'https://computer.cdu.edu.cn/info/1036/999800.htm',
                          Article('合成通知', '2026-10-18', '本条为软件测试用合成通知，学生可以查阅具体办理事项与原文说明。'),
                          seen_at=(now-timedelta(hours=73)).isoformat())
    before = hashlib.sha256(raw.read_bytes()).hexdigest()
    observed = database_snapshot(raw, now)
    assert observed['health_older_than_72h'] == 1
    assert observed['maximum_health_age_seconds'] == 73*3600
    assert observed['rechecks']['due'] == 1
    assert observed['rechecks']['oldest_due_seconds'] == 49*3600
    assert hashlib.sha256(raw.read_bytes()).hexdigest() == before
    assert list(tmp_path.iterdir()) == [raw]


def test_missing_database_is_reported_without_creating_a_replacement(tmp_path):
    raw = tmp_path/'missing.sqlite3'
    result = snapshot(raw, tmp_path/'state')
    assert result['raw']['status'] == 'unavailable'
    assert result['consistency'] == {'all': None, 'featured': None}
    assert not raw.exists() and not (tmp_path/'state').exists()


def test_locked_database_is_unavailable_and_report_cannot_overwrite_state(tmp_path):
    raw = tmp_path/'notices.sqlite3'
    with Store(raw):
        pass
    before = raw.read_bytes()
    with pytest.raises(FileExistsError):
        write_new(raw, {'would': 'destroy state'})
    assert raw.read_bytes() == before
    db = sqlite3.connect(raw)
    try:
        db.execute('BEGIN EXCLUSIVE')
        result = snapshot(raw, tmp_path/'state')
        assert result['raw']['status'] == 'unavailable'
        assert result['raw']['error'] == 'OperationalError'
    finally:
        db.rollback(); db.close()


def test_live_round_limit_cannot_be_extended_by_calling_the_function(tmp_path):
    with pytest.raises(ValueError):
        live(tmp_path/'four-rounds', 4)
    assert not (tmp_path/'four-rounds').exists()


def test_logical_snapshot_detects_schema_or_notice_changes(tmp_path):
    raw = tmp_path/'notices.sqlite3'
    with Store(raw):
        pass
    before = logical_digests(raw)
    with sqlite3.connect(raw) as db:
        db.execute('PRAGMA user_version=999')
    assert logical_digests(raw)['__schema__'] != before['__schema__']


def test_saved_round_recovery_restarts_process_without_any_collection(tmp_path, monkeypatch, loopback_only):
    import scripts.m10_local_acceptance as acceptance
    raw = tmp_path/'notices.sqlite3'
    with Store(raw) as store:
        store.sync_sources(load_sources())
        store.save_notice('computer', 'https://computer.cdu.edu.cn/info/1036/999799.htm',
                          Article('合成恢复通知', '2026-10-21', '本条为合成进程恢复验收，学生可查看教务办理事项和具体安排。'))
    from cdu_radar.async_queue import JobQueue
    with JobQueue(tmp_path/'jobs.sqlite3', raw):
        pass
    monkeypatch.setattr(acceptance, 'collect_cycle', lambda *a, **k: pytest.fail('Recovery must never collect'))
    result = recover_live(tmp_path, [{'round': n, 'collection': {'requests': 0}} for n in (1, 2)])
    assert result['publisher_process_restart'] and result['listeners_stopped']
    assert result['guid_pubdate_preserved'] and result['raw_and_jobs_logical_state_preserved']
    assert result['official_requests'] == result['model_requests'] == 0
