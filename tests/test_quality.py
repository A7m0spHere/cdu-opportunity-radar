import hashlib
import json
from pathlib import Path
import os
import sqlite3
from datetime import timedelta

import pytest

from cdu_radar.cli import main
from cdu_radar.models import Article, ParseError
from cdu_radar.quality import build_report, render_markdown
from test_auto_publish import add, NOW, URL, BODY


def database(store):
    return Path(store.db.execute('PRAGMA database_list').fetchone()[2])


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_report_is_readonly_no_migration_or_network(store,monkeypatch):
    add(store)
    path=database(store);before=digest(path);files=set(path.parent.iterdir())
    monkeypatch.setattr('cdu_radar.storage.Store.__init__',lambda *a,**k:pytest.fail('Report must not initialize Store'))
    report=build_report(path,as_of=NOW+timedelta(hours=1))
    assert report['read_only'] and report['network_requests']==report['llm_calls']==0
    assert digest(path)==before and set(path.parent.iterdir())==files
    assert report['snapshot']['qualified']==1
    assert store.db.execute('PRAGMA user_version').fetchone()[0]==1


def test_empty_and_missing_database_are_distinct(store,tmp_path):
    report=build_report(database(store),as_of=NOW)
    assert report['snapshot']['raw_unique_notices']==report['snapshot']['qualified']==0
    assert report['source_health']['never_collected']==report['source_health']['denominator']
    assert report['source_health']['success_unverified_or_expired']==report['source_health']['denominator']
    assert report['latency']['exact_hours']['median'] is None
    missing=tmp_path/'missing.sqlite3'
    with pytest.raises(ValueError,match='does not exist'):
        build_report(missing)
    assert not missing.exists()


def test_missing_schema_is_not_migrated(tmp_path):
    path=tmp_path/'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE notices (id INTEGER)')
    before=digest(path)
    with pytest.raises(ValueError,match='never migrates'):
        build_report(path)
    assert digest(path)==before


def test_date_only_latency_is_not_precise_and_unknown_date_retained(store):
    add(store)
    add(store,url=URL.replace('990001','990002'),published=None)
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['qualified']==2
    assert report['latency']['exact_hours']['samples']==0
    assert report['latency']['exact_uncomputable_missing_source_time_or_timezone']==2
    assert report['latency']['calendar_day_lag']['samples']==1
    assert report['latency']['missing_dates_or_collection_time']==1


def test_unknown_registration_and_history_overlap_not_added(store):
    add(store,body='这是一条没有任何报名截止日期的合成历史活动通知，供同学查询资料。',published='2024-01-01')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    counts=report['snapshot']
    assert counts['qualified']==counts['historical']==counts['registration_unknown']==1
    assert counts['possible']==counts['opening_evidence_supported']==0


def test_reposts_use_global_unique_url_and_overlapping_source_counts(store):
    add(store)
    store.save_notice('business',URL,Article('合成校园竞赛报名','2026-10-15',BODY),seen_at=NOW.isoformat())
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['raw_unique_notices']==report['snapshot']['qualified']==1
    sources={s['source_id']:s for s in report['sources']}
    assert sources['computer']['qualified_unique_notices']==sources['business']['qualified_unique_notices']==1


def test_parse_failure_and_72_hour_expiry_separated(store):
    add(store)
    add(store,url=URL.replace('990001','990002'))
    store.fail_notice(URL,ParseError('synthetic changed template'),at=NOW.isoformat())
    report=build_report(database(store),as_of=NOW+timedelta(hours=73))
    assert report['snapshot']['qualified']==0
    assert report['snapshot']['excluded']=={'parse_failed':1,'source_check_expired':1}


def test_source_failure_does_not_invent_all_articles_failed(store):
    add(store)
    store.finish_run('computer',NOW.isoformat(),dict(inserted=0,updated=0,unchanged=0,failed=1,parse_failures=1),'synthetic list error')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1),window_hours=72)
    assert report['snapshot']['qualified']==1
    assert report['source_health']['latest_attempt_failed']==1
    # finish_run uses the real clock; report time filtering is explicit.
    report=build_report(database(store),window_hours=72)
    assert report['window']['collection_event_totals']['failed']==1


def test_window_is_half_open_and_updates_are_distinct_from_events(store):
    start=NOW-timedelta(hours=24)
    add(store,at=start)
    add(store,url=URL.replace('990001','990002'),at=NOW)
    add(store,body=BODY+'\n实质修订说明。',at=NOW-timedelta(hours=1))
    report=build_report(database(store),as_of=NOW,window_hours=24)
    window=report['window']
    assert window['new_unique_notices']==1
    assert window['latest_updated_unique_notices']==1
    assert window['new_or_updated_unique_notices']==1


@pytest.mark.parametrize('opening,expected', [
    ('即日起正在报名。',1),('并非报名已开始。',0),
    ('即日起受理比赛咨询，报名开放时间另行通知。',0),
    ('请勿误以为报名已开始。',0),('关于正在报名的说法尚未确认。',0),
    ('本次竞赛即日起接受报名。',1)])
def test_opening_requires_positive_body_evidence_not_future_deadline_alone(store,opening,expected):
    add(store,body='报名对象：我校全体本科生。'+opening+'报名截止：2026年10月20日。请按官方要求提供材料。')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['possible']==1
    assert report['snapshot']['opening_evidence_supported']==expected


def test_wal_refused_without_creating_new_sidecars(store):
    path=database(store)
    store.db.execute('PRAGMA journal_mode=WAL')
    files=set(path.parent.iterdir())
    with pytest.raises(ValueError,match='rollback'):
        build_report(path)
    assert set(path.parent.iterdir())==files


def test_cli_new_file_and_refuse_database_or_existing_output(store,tmp_path,capsys):
    add(store)
    path=database(store);before=digest(path)
    output=tmp_path/'quality.json'
    args=['quality-report','--db',str(path),'--as-of',NOW.isoformat(),'--format','json','--output',str(output)]
    assert main(args)==0
    report=json.loads(output.read_text(encoding='utf-8'))
    assert report['schema_version']==1 and '质量报告' in render_markdown(report)
    assert main(args)==2
    assert main(['quality-report','--db',str(path),'--output',str(path)])==2
    if os.name=='nt':
        sidecar=path.with_name(path.name.upper()+'-JOURNAL')
        assert main(['quality-report','--db',str(path),'--output',str(sidecar)])==2
        assert not sidecar.exists()
    assert digest(path)==before


@pytest.mark.parametrize('target,expected',[
    ('橙园社区全体宿管老师',0),('宿管老师和学生骨干',1),('不限于教师',1),
    ('教师除外',1),('不要求教师身份',1)])
def test_explicit_staff_target_excluded_without_guessing_mixed_audience(store,target,expected):
    add(store,body='合成校园竞赛通知。\n三、参与对象：\n'+target+'\n四、有关安排另行通知，本文没有明确报名截止日期。')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['participation_candidates']==expected
    assert report['snapshot']['non_student_target']==(not expected)
    assert report['snapshot']['qualified']==1


def test_credit_disclosure_is_not_a_lecture_entry_point(store):
    add(store,title='合成讲座教育素质学分认定情况公示',body='这是讲座学分提交情况的合成公示说明，供同学核对已经提交的材料，不是新的讲座报名通知。')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['qualified']==report['snapshot']['administrative']==1
    assert report['snapshot']['participation_candidates']==0


def test_quality_output_refuses_dangling_symlink_without_creating_target(store,tmp_path):
    path=database(store);target=tmp_path/'must-not-create.json';link=tmp_path/'report-link.json'
    try:
        link.symlink_to(target)
    except (OSError,NotImplementedError):
        pytest.skip('Platform does not permit creating symbolic links')
    assert main(['quality-report','--db',str(path),'--output',str(link)])==2
    assert link.is_symlink() and not target.exists()


def test_start_date_does_not_override_explicit_pending_opening(store):
    add(store,body='报名对象：我校全体本科生。报名开始：2026年10月14日。报名截止：2026年10月20日。报名开放时间另行通知。')
    report=build_report(database(store),as_of=NOW+timedelta(hours=1))
    assert report['snapshot']['opening_evidence_supported']==0
