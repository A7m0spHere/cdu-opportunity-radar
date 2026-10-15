"""Offline human approval and RSS invariants; no real publication or messages."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree as ET

import feedparser
import pytest

from cdu_radar.analysis_models import ManualLabel, reference_datetime
from cdu_radar.analysis_service import analyze_one
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.approved_rss import export_approved,render
from cdu_radar.cli import main
from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.llm_workflow import readonly_repository
from cdu_radar.models import Article,ArticleLink
from cdu_radar.publication import PublicationRepository,migrate,snapshot
from cdu_radar.rss import build_rss
from cdu_radar.storage import Store
from test_llm import seed,BODY
from test_collect_storage import transport_for

NOW=reference_datetime('2026-10-14')


def setup(store,body=BODY,ident=1):
    nid=seed(store,body,ident);repo=AnalysisRepository(store)
    return nid,PublicationRepository(repo)


def draft(pub,nid):
    n,_=snapshot(pub.db,nid)
    return {'title':'人工确认的实践竞赛','summary':'跨学科实践与学习机会；详情请阅读官方安排。',
        'source_url':n['canonical_url'],'participation_quote':n['title'],'valid_until':'2026-10-30T23:59:59+08:00',
        'facts':{f:None for f in ['registration_deadline','registration_start_date','internal_deadline','eligibility','ai_use_policy']}}


def decide(pub,nid,status='approved',payload=None,reason='已查看原文及未知事项',confirm=None,**opts):
    d=payload or draft(pub,nid) if status=='approved' else None
    plan=pub.preview(nid,status,'test-admin',reason,d,True,opts.get('ack_correction',False),NOW)
    migrate(pub.db)
    with pub.db:
        pub.db.execute('BEGIN IMMEDIATE')
        result=pub.decide(nid,status,'test-admin',reason,confirm=confirm or plan['confirmation_token'],draft=d,
            ack_unknown=True,ack_correction=opts.get('ack_correction',False),now=NOW)
    return result,plan


def export(pub,path,now=NOW):
    plan=pub.feed_plan(now)
    return export_approved(pub,path,confirm=plan['plan_hash'],now=now)


def test_evaluation_positive_and_legacy_approval_never_publish(store):
    nid,pub=setup(store);n=pub.repository.input(nid)
    with pub.db:pub.repository.add_label(ManualLabel(nid,n.content_hash,n.input_hash,'human','synthetic-admin',worth_recommending=True))
    pub.repository.review(nid,'approved','legacy analysis decision','synthetic-admin')
    assert pub.feed_plan(NOW)['items']==[] and pub.effective(nid,NOW)['review_status']=='pending'
    shown=pub.show(nid,NOW)
    assert shown['evaluation_labels'][0]['worth_recommending'] is True
    assert shown['evaluation_labels_are_publication_approval'] is False


def test_llm_failure_does_not_hide_raw_notice_or_block_manual_approval(store):
    nid,pub=setup(store)
    class Broken:
        version='offline-broken';model_name='fixture'
        def analyze(self,*args,**kwargs):raise ValueError('fixture failure')
    result,_=analyze_one(pub.repository,nid,Broken(),NOW)
    assert result.analysis_state=='failed'
    shown=pub.show(nid,NOW)
    assert shown['notice']['content'] and shown['analyses'][0]['analysis_state']=='failed'
    decide(pub,nid)
    assert pub.feed_plan(NOW)['new_publications']==1


@pytest.mark.parametrize('status',['rejected','needs_revision','revoked'])
def test_non_approved_decisions_stay_out_of_feed(store,status):
    nid,pub=setup(store);decide(pub,nid,status)
    assert pub.feed_plan(NOW)['items']==[]
    assert pub.history(nid)['reviews'][0]['review_status']==status


def test_confirmed_approval_and_repeat_are_idempotent(store,tmp_path):
    nid,pub=setup(store);first,plan=decide(pub,nid)
    again,_=decide(pub,nid,confirm=plan['confirmation_token'])
    assert again['idempotent'] and first['review_id']==again['review_id']
    assert len(pub.history(nid)['reviews'])==1
    out=tmp_path/'approved.xml';a=export(pub,out);before=out.read_bytes();row=pub.current(nid)
    assert a['new_publications']==1 and row['published_at']==NOW.isoformat()
    b=export(pub,out,reference_datetime('2026-10-15'))
    assert b['new_publications']==0 and before==out.read_bytes()
    f=feedparser.parse(before)
    assert not f.bozo and len(f.entries)==1 and f.entries[0].id==row['guid']
    assert f.entries[0].published_parsed.tm_year==2026
    assert '未知' in f.entries[0].summary and row['approved_title']==f.entries[0].title


def test_old_confirmation_cannot_execute_different_action(store):
    nid,pub=setup(store);_,plan=decide(pub,nid)
    with pytest.raises(ValueError,match='different review operation'):
        decide(pub,nid,'revoked',confirm=plan['confirmation_token'])
    assert pub.effective(nid,NOW)['valid']


@pytest.mark.parametrize('change',['content','title','date','attachment'])
def test_source_version_changes_withhold_old_snapshot_without_erasing_history(store,change,tmp_path):
    nid,pub=setup(store);decide(pub,nid);export(pub,tmp_path/'approved.xml')
    old=dict(pub.current(nid));history=pub.history(nid);n,_=snapshot(pub.db,nid)
    article=Article(n['title'],n['published_at'],n['content'])
    article=replace(article,**{'content':'修订说明。'+article.content} if change=='content' else {'title':'修订后的竞赛报名'} if change=='title' else {'published_at':'2026-10-02'} if change=='date' else {'attachments':(ArticleLink('规则附件','https://computer.cdu.edu.cn/rules.pdf'),)})
    store.save_notice('computer',n['canonical_url'],article)
    assert pub.effective(nid,NOW)['effective_review_status']=='needs_revision'
    assert not pub.feed_plan(NOW)['items'] and pub.queue(NOW)[0]['notice_id']==nid
    assert pub.history(nid)['reviews'][0]['approved_title']==old['approved_title']
    assert pub.history(nid)==history  # M4-B preserves legacy approval history as read-only.
    export(pub,tmp_path/'approved.xml')
    assert not feedparser.parse((tmp_path/'approved.xml').read_bytes()).entries


def test_reverting_source_does_not_automatically_restore_approval(store):
    nid,pub=setup(store);decide(pub,nid);n,_=snapshot(pub.db,nid)
    store.save_notice('computer',n['canonical_url'],Article(n['title'],n['published_at'],'新内容。'+n['content']))
    store.save_notice('computer',n['canonical_url'],Article(n['title'],n['published_at'],n['content']))
    assert not pub.effective(nid,NOW)['valid']


def test_last_seen_and_model_reanalysis_do_not_withhold_human_publication(store):
    nid,pub=setup(store);decide(pub,nid);version=pub.current(nid)['notice_version_hash'];n,_=snapshot(pub.db,nid)
    store.save_notice('computer',n['canonical_url'],Article(n['title'],n['published_at'],n['content']),'2026-10-15T00:00:00+00:00')
    analyze_one(pub.repository,nid,reference_time=NOW)
    from cdu_radar.rule_analysis import RuleAnalyzer
    class AlternateRules(RuleAnalyzer):
        def __init__(self):super().__init__();self.version='offline-alternate-prediction-version'
    analyze_one(pub.repository,nid,AlternateRules(),NOW)
    assert pub.effective(nid,NOW)['valid'] and pub.current(nid)['notice_version_hash']==version
    assert len(pub.history(nid)['events'])==1


def test_stale_review_confirmation_cannot_approve_new_body(store):
    nid,pub=setup(store);d=draft(pub,nid)
    p=pub.preview(nid,'approved','test-admin','已查看原文及未知事项',d,True,False,NOW)
    n,_=snapshot(pub.db,nid);store.save_notice('computer',n['canonical_url'],Article(n['title'],n['published_at'],'新增资格说明。'+n['content']))
    with pytest.raises(ValueError,match='stale'):decide(pub,nid,payload=d,confirm=p['confirmation_token'])
    assert not pub.feed_plan(NOW)['items']


def test_revoke_and_explicit_correction_keep_guid_and_first_pubdate(store,tmp_path):
    nid,pub=setup(store);decide(pub,nid);path=tmp_path/'approved.xml';export(pub,path);old=pub.current(nid)
    decide(pub,nid,'revoked',reason='资格公告更正，暂停发布')
    assert pub.current(nid)['revoked_at'] and not pub.feed_plan(NOW)['items']
    updated=draft(pub,nid);updated['summary']='实践安排已有实质更正，请重新核验原文。'
    with pytest.raises(ValueError,match='ack-correction'):decide(pub,nid,payload=updated)
    decision,_=decide(pub,nid,payload=updated,ack_correction=True)
    assert decision['correction_required']
    export(pub,path)
    current=pub.current(nid)
    assert old['guid']==current['guid'] and old['published_at']==current['published_at']
    assert len(pub.history(nid)['reviews'])==3
    assert pub.feed_plan(NOW)['new_publications']==0


def test_new_review_of_same_published_item_does_not_count_as_new_message(store,tmp_path):
    nid,pub=setup(store);decide(pub,nid);export(pub,tmp_path/'approved.xml')
    decide(pub,nid,reason='再次确认，内容保持原样')
    assert pub.feed_plan(NOW)['new_publications']==0


def test_approval_expiry_removes_item_even_if_source_unchanged(store,tmp_path):
    nid,pub=setup(store);decide(pub,nid)
    later=reference_datetime('2026-11-02')
    assert not pub.effective(nid,later)['valid'] and pub.queue(later)
    export(pub,tmp_path/'approved.xml',later)
    assert not feedparser.parse((tmp_path/'approved.xml').read_bytes()).entries


def test_unknown_ack_and_supported_fact_constraints(store):
    nid,pub=setup(store);d=draft(pub,nid)
    with pytest.raises(ValueError,match='ack-unknown'):pub.preview(nid,'approved','admin','核验',d,False,False,NOW)
    d['facts']['registration_deadline']={'value':'2026-11-01','quote':'报名截止：2026年11月1日'}
    d['facts']['eligibility']={'value':'参赛对象：我校本科生','quote':'参赛对象：我校本科生'}
    d['facts']['ai_use_policy']={'value':'explicitly_allowed','quote':'允许使用AI'}
    decision,_=decide(pub,nid,payload=d)
    saved=json.loads(pub.current(nid)['approved_payload'])
    assert saved['facts']['registration_deadline']['value']=='2026-11-01'
    assert 'eligibility' not in saved['unknown_fields']


@pytest.mark.parametrize('mode',['unsupported_year','wrong_role','permission','partial_quote','eligibility_inference','prose_date','source_url','expired_until'])
def test_draft_cannot_turn_missing_or_wrong_facts_into_verified_claims(store,mode):
    nid,pub=setup(store);d=draft(pub,nid)
    if mode=='unsupported_year':d['facts']['registration_deadline']={'value':'2027-11-01','quote':'报名截止：2026年11月1日'}
    if mode=='wrong_role':d['facts']['internal_deadline']={'value':'2026-11-01','quote':'报名截止：2026年11月1日'}
    if mode=='permission':d['facts']['ai_use_policy']={'value':'explicitly_prohibited','quote':'允许使用AI'}
    if mode=='partial_quote':d['facts']['ai_use_policy']={'value':'explicitly_allowed','quote':'使用AI'}
    if mode=='eligibility_inference':d['facts']['eligibility']={'value':'全校学生均可参加','quote':'我校本科生'}
    if mode=='prose_date':d['summary']='报名截止：2026年11月1日。'
    if mode=='source_url':d['source_url']='https://unverified.example/'
    if mode=='expired_until':d['valid_until']='2020-01-01T00:00:00+08:00'
    with pytest.raises(ValueError):pub.preview(nid,'approved','admin','已核实',d,True,False,NOW)
    assert not pub.feed_plan(NOW)['items']


def test_ai_picture_permission_cannot_become_full_permission_or_lose_scope(store):
    nid,pub=setup(store,'参赛对象：我校本科生。参赛者可使用AI进行图片制作。')
    d=draft(pub,nid);d['facts']['ai_use_policy']={'value':'explicitly_allowed','quote':'参赛者可使用AI进行图片制作'}
    with pytest.raises(ValueError):pub.validate_draft(nid,d,NOW)
    d['facts']['ai_use_policy']['value']='partially_allowed'
    assert pub.validate_draft(nid,d,NOW)['facts']['ai_use_policy']['value']=='partially_allowed'


@pytest.mark.parametrize('body',['参赛对象：我校本科生。报名截止：2025年11月1日。','参赛对象：我校本科生。报名已结束。'])
def test_known_closed_source_cannot_bypass_date_check_by_omitting_facts(store,body):
    nid,pub=setup(store,body)
    with pytest.raises(ValueError):pub.validate_draft(nid,draft(pub,nid),NOW)


def test_empty_feed_and_chinese_special_characters_are_valid(store,tmp_path):
    nid,pub=setup(store);migrate(pub.db)
    out=tmp_path/'approved.xml';report=export(pub,out)
    assert report['items']==0 and not feedparser.parse(out.read_bytes()).bozo
    d=draft(pub,nid);d['title']='中文 & <测试> "竞赛"';d['summary']='实践：中文 & <b>仅作为文本</b>。'
    decide(pub,nid,payload=d);export(pub,out)
    parsed=feedparser.parse(out.read_bytes());assert not parsed.bozo and parsed.entries[0].title==d['title']
    assert '&lt;b&gt;' in parsed.entries[0].summary


def test_failed_atomic_replace_and_restart_reuse_reserved_identity_time(store,tmp_path,monkeypatch):
    nid,pub=setup(store);decide(pub,nid);out=tmp_path/'approved.xml'
    from cdu_radar import approved_rss
    original=approved_rss.os.replace
    monkeypatch.setattr(approved_rss.os,'replace',lambda *a:(_ for _ in ()).throw(OSError('simulated replace failure')))
    with pytest.raises(OSError):export(pub,out)
    reserved=pub.current(nid)
    assert not out.exists() and reserved['publication_status']=='ready' and not list(tmp_path.glob('.approved-*.tmp'))
    monkeypatch.setattr(approved_rss.os,'replace',original)
    dbpath=Path(store.db.execute('PRAGMA database_list').fetchone()[2])
    with readonly_repository(dbpath) as repo:assert PublicationRepository(repo).current(nid)['guid']==reserved['guid']
    result=export(pub,out,reference_datetime('2026-10-15'))
    assert result['new_publications']==1 and pub.current(nid)['published_at']==reserved['published_at']


def test_multiple_new_items_and_unapproved_history_never_auto_publish(store,tmp_path):
    for i in range(1,37):nid,pub=setup(store,ident=i)
    migrate(pub.db);assert not pub.feed_plan(NOW)['items']
    for nid in [1,2]:decide(pub,nid)
    report=export(pub,tmp_path/'approved.xml')
    assert report['items']==report['new_publications']==2
    assert len(set(report['guids']))==2 and len(pub.queue(NOW))==34


@pytest.mark.parametrize('version',[1,2,4,7])
def test_append_only_migration_preserves_raw_labels_reviews_cache_and_original_guids(store,version):
    nid,pub=setup(store);analyze_one(pub.repository,nid,reference_time=NOW)
    n=pub.repository.input(nid)
    with pub.db:pub.repository.add_label(ManualLabel(nid,n.content_hash,n.input_hash,'human','synthetic',worth_recommending=True))
    pub.repository.review(nid,'approved','old analysis only','synthetic')
    tables=['notices','notice_links','manual_labels','analysis_reviews','notice_analyses']
    before={t:[tuple(r) for r in pub.db.execute('SELECT * FROM '+t)] for t in tables}
    guids=ET.fromstring(build_rss(store)).findall('./channel/item/guid')
    pub.db.execute(f'PRAGMA user_version={version}');pub.db.commit();migrate(pub.db);migrate(pub.db)
    assert pub.db.execute('PRAGMA user_version').fetchone()[0]==max(5,version)
    assert all([tuple(r) for r in pub.db.execute('SELECT * FROM '+t)]==values for t,values in before.items())
    assert [g.text for g in guids]==[g.text for g in ET.fromstring(build_rss(store)).findall('./channel/item/guid')]
    assert pub.db.execute('SELECT count(*) FROM publication_reviews').fetchone()[0]==0


def test_readonly_cli_previews_do_not_migrate_or_change_database(store,tmp_path,capsys):
    nid,pub=setup(store);path=Path(pub.db.execute('PRAGMA database_list').fetchone()[2]);before=hashlib.sha256(path.read_bytes()).hexdigest()
    for cmd in [['review','list'],['review','show',str(nid)],['review','history',str(nid)],['review','template',str(nid)],['export-approved-rss','--output',str(tmp_path/'approved.xml')]]:
        assert main(cmd+['--db',str(path)])==0;assert json.loads(capsys.readouterr().out) is not None
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before and not (tmp_path/'approved.xml').exists()


def test_cli_requires_exact_confirmation_and_protects_raw_feed(store,tmp_path,capsys):
    nid,pub=setup(store);path=Path(pub.db.execute('PRAGMA database_list').fetchone()[2])
    d=tmp_path/'draft.json';d.write_text(json.dumps(draft(pub,nid),ensure_ascii=False),encoding='utf-8')
    cmd=['review','approve',str(nid),'--db',str(path),'--draft',str(d),'--reviewer','admin','--reason','已核验','--ack-unknown']
    assert main(cmd)==0;plan=json.loads(capsys.readouterr().out)
    before=hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(cmd+['--confirm','bad-token'])==2;capsys.readouterr()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert main(cmd+['--confirm',plan['confirmation_token']])==2;capsys.readouterr()
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    assert main(['export-approved-rss','--db',str(path),'--output',str(tmp_path/'feed.xml')])==2;capsys.readouterr()
    out=tmp_path/'approved.xml';args=['export-approved-rss','--db',str(path),'--output',str(out)]
    assert main(args)==0;p=json.loads(capsys.readouterr().out)
    assert p['new_publications']==0 and not out.exists()
    assert main(args+['--confirm',p['plan_hash']])==2
    assert not out.exists()


def test_offline_collection_to_rule_analysis_to_human_publication_to_feedparser(store,sources,sample,tmp_path):
    selected=[s for s in sources if s.id=='computer']
    with Fetcher(transport=transport_for(sample),interval=0) as fetcher:
        results=collect(store,fetcher,selected,limit=1)
    assert results[0]['inserted']==1
    repo=AnalysisRepository(store);nid=repo.ids()[0]
    analyze_one(repo,nid,reference_time=NOW)
    pub=PublicationRepository(repo);shown=pub.show(nid,NOW)
    assert shown['analyses'] and not pub.feed_plan(NOW)['items']
    decide(pub,nid);out=tmp_path/'approved.xml';export(pub,out)
    parsed=feedparser.parse(out.read_bytes())
    assert not parsed.bozo and len(parsed.entries)==1 and parsed.entries[0].link==shown['notice']['canonical_url']


def test_feed_preview_confirmation_invalidated_by_new_approval(store,tmp_path):
    nid,pub=setup(store);decide(pub,nid);old=pub.feed_plan(NOW)['plan_hash']
    other,_=setup(store,ident=2);decide(pub,other)
    out=tmp_path/'approved.xml'
    with pytest.raises(ValueError,match='stale'):export_approved(pub,out,confirm=old,now=NOW)
    assert not out.exists() and pub.current(nid)['published_at'] is None


def test_raw_body_change_even_with_incorrect_stored_hash_is_not_valid_approval(store):
    nid,pub=setup(store);decide(pub,nid)
    with pub.db:pub.db.execute('UPDATE notices SET content=content||? WHERE id=?',(' Changed outside collector.',nid))
    assert not pub.effective(nid,NOW)['valid'] and not pub.feed_plan(NOW)['items']


def test_cli_reject_defer_revoke_confirmation_and_history(store,capsys):
    nid,pub=setup(store);path=Path(pub.db.execute('PRAGMA database_list').fetchone()[2])
    for name,status in [('reject','rejected'),('defer','needs_revision'),('revoke','revoked')]:
        cmd=['review',name,str(nid),'--db',str(path),'--reviewer','local-admin','--reason','合成测试决策']
        assert main(cmd)==0;p=json.loads(capsys.readouterr().out)
        assert p['preview'] and main(cmd+['--confirm',p['confirmation_token']])==2
        capsys.readouterr()
    assert main(['review','history',str(nid),'--db',str(path)])==0
    assert len(json.loads(capsys.readouterr().out)['reviews'])==0


def test_existing_non_approved_xml_and_invalid_export_token_are_protected(store,tmp_path,capsys):
    nid,pub=setup(store);path=Path(pub.db.execute('PRAGMA database_list').fetchone()[2])
    out=tmp_path/'original.xml';out.write_bytes(build_rss(store));original=out.read_bytes()
    assert main(['export-approved-rss','--db',str(path),'--output',str(out)])==2
    capsys.readouterr();assert out.read_bytes()==original
    dbhash=hashlib.sha256(path.read_bytes()).hexdigest()
    assert main(['export-approved-rss','--db',str(path),'--output',str(tmp_path/'approved.xml'),'--confirm','wrong'])==2
    assert hashlib.sha256(path.read_bytes()).hexdigest()==dbhash
