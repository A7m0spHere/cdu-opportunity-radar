"""Merge blockers: source expiry cannot be omitted; advisory corruption is isolated."""
import json
from pathlib import Path

import feedparser
import pytest

from cdu_radar.analysis_models import ManualLabel, reference_datetime
from cdu_radar.analysis_service import analyze_one
from cdu_radar.cli import main
from cdu_radar.publication import snapshot
from cdu_radar.rule_analysis import deadline_moment
from test_publication import setup, draft, decide, export, NOW


def legacy_long_approval(pub, ident):
    """Only a temporary test DB: emulate a pre-fix approval without rewriting real history."""
    decide(pub,ident)
    row=pub.current(ident);a=json.loads(row['approved_payload'])
    a['valid_until']='2030-10-30T23:59:59+08:00'
    with pub.db:
        pub.db.execute('UPDATE publication_reviews SET approved_payload=? WHERE id=?',(json.dumps(a,ensure_ascii=False),row['id']))
    return pub.current(ident)['approved_payload']


def test_omitted_date_fact_cannot_extend_unique_source_deadline(store,tmp_path):
    ident,pub=setup(store);d=draft(pub,ident);d['valid_until']='2030-10-30T23:59:59+08:00'
    result,plan=decide(pub,ident,payload=d)
    assert plan['approved']['valid_until']==deadline_moment('2026-11-01').isoformat()
    assert plan['approved']['facts']['registration_deadline'] is None
    assert 'registration_deadline' in plan['approved']['unknown_fields']
    assert result['publication']['valid']
    out=tmp_path/'approved.xml';export(pub,out,NOW)
    assert '未知' in feedparser.parse(out.read_bytes()).entries[0].summary
    later=reference_datetime('2026-11-02');export(pub,out,later)
    assert not feedparser.parse(out.read_bytes()).entries
    assert pub.queue(later)[0]['review_status']=='needs_revision'


def test_legacy_approval_is_rechecked_at_export_without_history_mutation(store,tmp_path):
    ident,pub=setup(store);original=legacy_long_approval(pub,ident)
    before=pub.history(ident)
    state=pub.effective(ident,'2026-11-02')
    assert not state['valid'] and state['invalid_reason']=='source_registration_expired'
    out=tmp_path/'approved.xml';report=export(pub,out,reference_datetime('2026-11-02'))
    assert report['items']==0 and report['excluded'][0]['reason']=='source_registration_expired'
    assert pub.current(ident)['approved_payload']==original and pub.history(ident)==before


@pytest.mark.parametrize('reference,valid',[
    ('2026-11-01T15:59:59+00:00',True),('2026-11-01T16:00:00+00:00',False)])
def test_source_deadline_uses_shanghai_end_of_day_for_old_approvals(store,reference,valid):
    ident,pub=setup(store);legacy_long_approval(pub,ident)
    assert pub.effective(ident,reference)['valid']==valid


@pytest.mark.parametrize('body,limit',[
    ('参赛对象：我校本科生。报名时间：2026年11月1日至12月1日。','2026-12-01'),
    ('参赛对象：我校本科生。校内报名截止：2026年10月20日；主办方截止：2026年11月1日。','2026-10-20'),
    ('参赛对象：我校本科生。报名截止：2026年11月1日，联系人：合成角色。','2026-11-01'),
])
def test_range_internal_and_contact_line_dates_constrain_unknown_drafts(store,body,limit):
    ident,pub=setup(store,body);d=draft(pub,ident);d['valid_until']='2030-10-30T23:59:59+08:00'
    plan=pub.preview(ident,'approved','fixture-admin','Offline review',d,True,False,NOW)
    assert plan['approved']['valid_until']==deadline_moment(limit).isoformat()
    assert all(v is None for v in plan['approved']['facts'].values())


@pytest.mark.parametrize('mention',[
    '发布日期：2025年11月1日。','比赛时间：2025年11月1日。',
    '报名截止暂定：2025年11月1日。','报名截止：11月1日。'])
def test_unrelated_tentative_or_yearless_dates_do_not_become_cutoffs(store,mention):
    ident,pub=setup(store,'参赛对象：我校本科生。'+mention)
    d=draft(pub,ident);d['valid_until']='2030-10-30T23:59:59+08:00'
    plan=pub.preview(ident,'approved','fixture-admin','Offline review',d,True,False,NOW)
    assert plan['approved']['valid_until']==d['valid_until']


def test_multiple_track_dates_stay_unknown_but_all_expired_routes_are_withheld(store):
    ident,pub=setup(store,'参赛对象：我校本科生。A赛道报名截止：2026年11月1日；B赛道报名截止：2026年12月1日。')
    d=draft(pub,ident);d['valid_until']='2030-10-30T23:59:59+08:00'
    result,plan=decide(pub,ident,payload=d)
    assert plan['approved']['facts']['registration_deadline'] is None
    assert plan['approved']['valid_until']==d['valid_until']
    assert result['publication']['valid'] and not pub.effective(ident,'2026-12-02')['valid']


@pytest.mark.parametrize('broken',['{invalid-private-fixture','null','[]','{}'])
def test_invalid_analysis_record_does_not_hide_good_records_or_original(store,broken):
    ident,pub=setup(store);good,_=analyze_one(pub.repository,ident,reference_time=NOW)
    n=pub.repository.input(ident)
    with pub.db:
        pub.db.execute('INSERT INTO notice_analyses (notice_id,content_hash,input_hash,analyzer_version,analyzed_at,model_name,payload) VALUES (?,?,?,?,?,?,?)',
            (ident,n.content_hash,n.input_hash,'fixture-invalid-cache',NOW.isoformat(),'fixture-model',broken))
    original=[tuple(r) for r in pub.db.execute('SELECT * FROM notice_analyses')]
    shown=pub.show(ident,NOW)
    assert shown['notice']['content'] and shown['analyses'][0]['analyzer_version']==good.analyzer_version
    assert shown['advisory_errors']==[{'table':'notice_analyses','record_id':2,'error_category':'invalid_json' if broken.startswith('{invalid') else 'invalid_record'}]
    assert broken not in json.dumps(shown['advisory_errors'])
    decide(pub,ident)
    assert pub.effective(ident,NOW)['valid']
    decide(pub,ident,'revoked')
    assert not pub.effective(ident,NOW)['valid']
    assert [tuple(r) for r in pub.db.execute('SELECT * FROM notice_analyses')]==original


def test_invalid_label_is_an_advisory_error_not_a_publication_decision(store):
    ident,pub=setup(store);n=pub.repository.input(ident)
    with pub.db:
        pub.repository.add_label(ManualLabel(ident,n.content_hash,n.input_hash,'human','synthetic',worth_recommending=True))
        pub.db.execute('INSERT INTO manual_labels (notice_id,content_hash,input_hash,label_origin,labeler,labeled_at,payload) VALUES (?,?,?,?,?,?,?)',
            (ident,n.content_hash,n.input_hash,'human','synthetic',NOW.isoformat(),'null'))
    original=[tuple(r) for r in pub.db.execute('SELECT * FROM manual_labels')]
    shown=pub.show(ident,NOW)
    assert shown['evaluation_labels'][0]['worth_recommending'] is True and not shown['publication']['valid']
    assert shown['advisory_errors'][0]['table']=='manual_labels'
    decide(pub,ident)
    assert [tuple(r) for r in pub.db.execute('SELECT * FROM manual_labels')]==original


def test_cli_show_approve_and_revoke_work_with_broken_advisory_cache(store,tmp_path,capsys):
    ident,pub=setup(store);n=pub.repository.input(ident)
    with pub.db:
        pub.db.execute('INSERT INTO notice_analyses (notice_id,content_hash,input_hash,analyzer_version,analyzed_at,payload) VALUES (?,?,?,?,?,?)',
            (ident,n.content_hash,n.input_hash,'fixture-corrupt',NOW.isoformat(),'{private-fixture-body'))
    path=Path(pub.db.execute('PRAGMA database_list').fetchone()[2])
    assert main(['review','show',str(ident),'--db',str(path)])==0
    shown=json.loads(capsys.readouterr().out);assert shown['notice']['content'] and shown['advisory_errors']
    d=tmp_path/'draft.json';d.write_text(json.dumps(draft(pub,ident)),encoding='utf-8')
    actions=[['review','approve',str(ident),'--draft',str(d),'--ack-unknown'],['review','revoke',str(ident)]]
    for action in actions:
        cmd=action+['--db',str(path),'--reviewer','fixture-admin','--reason','Offline decision']
        assert main(cmd)==0;preview=json.loads(capsys.readouterr().out)
        assert preview['advisory_errors']
        assert main(cmd+['--confirm',preview['confirmation_token']])==2;capsys.readouterr()
    assert not pub.effective(ident,NOW)['valid']
