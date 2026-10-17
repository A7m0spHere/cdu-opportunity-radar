"""Synthetic offline data-tool checks. No proxy/server/deployment is started."""
import argparse
from datetime import timedelta
import json
from pathlib import Path
import sqlite3
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cdu_radar.analysis_models import reference_datetime
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_cli import configuration
from cdu_radar.async_contract import mock_payload, validate
from cdu_radar.async_queue import JobQueue
from cdu_radar.auto_service import AutoFeedController
from cdu_radar.backup_state import sha256
from cdu_radar.models import Article
from cdu_radar.sources import load_sources
from cdu_radar.storage import Store
from scripts.validate_m9_design import validate as validate_draft


def rows(path, table):
    with sqlite3.connect(path) as db:
        return db.execute('SELECT * FROM ' + table + ' ORDER BY rowid').fetchall()


def run(directory):
    root = Path(directory).resolve()
    root.mkdir(parents=True, mode=0o700, exist_ok=False)
    raw, jobs = root / 'synthetic.sqlite3', root / 'synthetic-jobs.sqlite3'
    now = reference_datetime('2026-10-17T12:00:00+08:00')
    analyzer, _ = configuration(None, True)
    cases = [('合成助学金通知', '本次助学金面向在校学生，保障学生权益，申请要求请查看原文。', 'high'),
             ('合成例行工作动态', '学院例行工作交流顺利结束，此为合成的软件验收样例。', 'low'),
             ('合成国家级科研成果获批新闻', '我校国家级重要科研成果正式获批，这是具有明确来源的历史进展报道。', 'high')]
    with Store(raw) as store:
        store.sync_sources(load_sources())
        for index, (title, content, _) in enumerate(cases, 1):
            date = (now - timedelta(days=400)).date() if index == 3 else now.date()
            store.save_notice('computer', f'https://computer.cdu.edu.cn/info/1036/{991000+index}.htm',
                              Article(title, date.isoformat(), content), seen_at=now.isoformat())
        repo = AnalysisRepository.__new__(AnalysisRepository); repo.store = store; repo.db = store.db
        with JobQueue(jobs, raw) as queue:
            queue.scan(analyzer)
            for index, (_, content, level) in enumerate(cases, 1):
                notice = repo.input(index)
                supplied, _ = analyzer.prepare(notice)
                proposal = mock_payload(supplied)
                proposal['importance'] = {'kind': 'inference', 'level': level, 'reason': 'synthetic backup check', 'quotes': [content]}
                checked = validate(proposal, notice, supplied, analyzer, now)
                queue.db.execute('UPDATE jobs SET state=?,result=? WHERE notice_id=?',
                                 (checked['state'], json.dumps(checked, ensure_ascii=False), index))
    state = root / 'http-state'
    controller = AutoFeedController(raw, state, data_mode='offline_replay', analysis_state=jobs,
                                    analysis_analyzer=analyzer, clock=lambda: now)
    try:
        assert controller.refresh()
        with controller.response_gate(controller.snapshot, '/rss/v1/featured.xml') as response:
            original = response.body
    finally:
        controller.close()
    before = {str(p): sha256(p) for p in (raw, jobs, state / 'subscriptions.sqlite3')}
    first = rows(state / 'subscriptions.sqlite3', 'first_featured')
    assert len(first) == 2
    tool = Path(__file__).resolve().with_name('backup_state.py')

    def cli(arguments):
        result = subprocess.run([sys.executable, str(tool), *arguments], capture_output=True,
                                text=True, encoding='utf-8', check=True, timeout=40)
        return json.loads(result.stdout)

    backup_report = cli(['backup', '--raw-db', str(raw), '--state-dir', str(state),
                         '--analysis-db', str(jobs), '--output', str(root / 'bundle'), '--quiesced'])
    restore_report = cli(['restore', '--bundle', str(root / 'bundle'), '--output', str(root / 'restored')])
    assert before == {p: sha256(Path(p)) for p in before}
    restored_raw, restored_jobs = Path(restore_report['raw_db']), Path(restore_report['analysis_db'])
    restored_state = Path(restore_report['state_dir'])
    assert rows(restored_state / 'subscriptions.sqlite3', 'collection') == []
    assert first == rows(restored_state / 'subscriptions.sqlite3', 'first_featured')
    assert rows(jobs, 'jobs') == rows(restored_jobs, 'jobs')
    assert rows(jobs, 'attempts') == rows(restored_jobs, 'attempts')
    assert rows(raw, 'notice_health') == rows(restored_raw, 'notice_health')
    restored = AutoFeedController(restored_raw, restored_state, data_mode='offline_replay',
                                  analysis_state=restored_jobs, analysis_analyzer=analyzer, clock=lambda: now)
    try:
        assert restored.snapshot is None and restored.refresh()
        with restored.response_gate(restored.snapshot, '/rss/v1/featured.xml') as response:
            assert response.body == original
    finally:
        restored.close()
    draft = validate_draft(Path(__file__).resolve().parents[1] / 'deploy/m9')
    assert draft['passed']
    report = {'kind': 'synthetic_offline_preparation', 'passed': True, 'raw_notices': 3,
              'featured_items': 2, 'backup': backup_report, 'restore': restore_report,
              'source_databases_unchanged': True, 'jobs_and_budget_rows_preserved': True,
              'health_timestamps_preserved': True, 'first_featured_preserved': True,
              'restored_featured_xml_equal_after_revalidation': True, 'draft_policy': draft,
              'network_requests': 0, 'model_calls': 0, 'human_truth_count': 0,
              'listeners_opened': 0, 'deployment_rehearsal_performed': False,
              'nginx_t_executed': False, 'linux_native_executed': False}
    (root / 'result.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': True, 'raw_notices': 3, 'featured_items': 2,
                      'draft_policy_checks': len(draft['checks']), 'listeners_opened': 0,
                      'deployment_rehearsal_performed': False}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    run(parser.parse_args().directory)
