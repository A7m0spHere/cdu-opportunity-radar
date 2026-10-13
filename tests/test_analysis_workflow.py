"""Synthetic temporary databases exercise manual workflows; no real truth labels."""
import csv
from dataclasses import asdict,replace
import json
from pathlib import Path
import sqlite3
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.analysis_models import ManualLabel,reference_datetime
from cdu_radar.analysis_service import analyze_all,analyze_one
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.cli import main
from cdu_radar.evaluation import evaluate
from cdu_radar.labels import COLUMNS,export_labels,import_labels
from cdu_radar.models import Article,ArticleLink
from cdu_radar.rss import build_rss
from cdu_radar.rule_analysis import RuleAnalyzer
from cdu_radar.storage import Store

ASOF=reference_datetime('2026-10-13')


def seed(store,title='AI应用大赛报名',content='参赛对象：我校本科生。允许使用AI。报名截止：2026年11月1日。',url_id=1):
    url=f'https://computer.cdu.edu.cn/info/1/{url_id}.htm'
    store.save_notice('computer',url,Article(title,'2026-10-01',content))
    return store.db.execute('SELECT id FROM notices WHERE canonical_url=?',(url,)).fetchone()[0]


def worksheet(path):
    with path.open(encoding='utf-8-sig',newline='') as f:return list(csv.DictReader(f))


def write_sheet(path,rows):
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=COLUMNS);writer.writeheader();writer.writerows(rows)


def test_same_input_hits_cache_and_time_state_is_refreshed(store):
    notice_id=seed(store,content='参赛对象：我校本科生。报名截止：2026年10月14日。')
    repo=AnalysisRepository(store)
    first,cached=analyze_one(repo,notice_id,reference_time=ASOF)
    second,cached=analyze_one(repo,notice_id,reference_time=reference_datetime('2026-10-15'))
    assert cached and first.analyzed_at==second.analyzed_at
    assert first.opportunity_status=='open' and second.opportunity_status=='closed'
    assert store.db.execute('SELECT count(*) FROM notice_analyses').fetchone()[0]==1


def test_body_title_and_attachment_changes_invalidate_cache(store):
    notice_id=seed(store);repo=AnalysisRepository(store)
    initial,_=analyze_one(repo,notice_id,reference_time=ASOF)
    for article in [Article('AI应用大赛报名','2026-10-01','参赛对象：我校本科生。报名截止：2026年12月1日。'),
                    Article('新标题数字媒体比赛','2026-10-01','参赛对象：我校本科生。报名截止：2026年12月1日。'),
                    Article('新标题数字媒体比赛','2026-10-01','参赛对象：我校本科生。报名截止：2026年12月1日。',attachments=(ArticleLink('规则.pdf','https://computer.cdu.edu.cn/a.pdf'),))]:
        store.save_notice('computer','https://computer.cdu.edu.cn/info/1/1.htm',article)
        result,cached=analyze_one(repo,notice_id,reference_time=ASOF)
        assert not cached and result.input_hash!=initial.input_hash
        initial=result
    assert store.db.execute('SELECT count(*) FROM notice_analyses').fetchone()[0]==4


def test_human_review_not_overwritten_by_new_analyzer(store):
    notice_id=seed(store);repo=AnalysisRepository(store)
    analyze_one(repo,notice_id,reference_time=ASOF)
    repo.review(notice_id,'approved','测试人工核验','test-reviewer')
    class Alternate(RuleAnalyzer):
        def __init__(self):super().__init__();self.version='test-analyzer-v2'
    result,_=analyze_one(repo,notice_id,Alternate(),ASOF)
    assert result.review_status=='approved'
    repo.review(notice_id,'rejected','测试人工拒绝','test-reviewer')
    result,_=analyze_one(repo,notice_id,RuleAnalyzer(),ASOF)
    assert result.review_status=='rejected'
    assert store.db.execute('SELECT count(*) FROM analysis_reviews').fetchone()[0]==2


def test_updated_content_requires_new_review_without_erasing_history(store):
    notice_id=seed(store);repo=AnalysisRepository(store)
    repo.review(notice_id,'approved','旧版审核','test-reviewer')
    store.save_notice('computer','https://computer.cdu.edu.cn/info/1/1.htm',Article('AI新比赛','2026-10-01','报名资格与日期待核实。'))
    result,_=analyze_one(repo,notice_id,reference_time=ASOF)
    assert result.review_status=='pending' and result.needs_manual_review
    assert store.db.execute("SELECT count(*) FROM analysis_reviews WHERE status='approved'").fetchone()[0]==1


@pytest.mark.parametrize('mode',['exception','automatic_approval','fabricated_fact'])
def test_analyzer_failures_keep_notice_and_do_not_log_secrets(store,mode):
    notice_id=seed(store);repo=AnalysisRepository(store)
    class Unsafe(RuleAnalyzer):
        def analyze(self,notice,*,reference_time):
            if mode=='exception':raise RuntimeError('sensitive-provider-response-example')
            output=super().analyze(notice,reference_time=reference_time)
            if mode=='automatic_approval':return replace(output,review_status='approved')
            from cdu_radar.analysis_models import Evidence
            return replace(output,evidence=(Evidence('organizer','fact','unprovided quote','fabricated'),))
    result,_=analyze_one(repo,notice_id,Unsafe(),ASOF)
    assert result.analysis_state=='failed' and result.review_status=='pending' and result.needs_manual_review
    assert 'sensitive-provider' not in json.dumps(result.to_dict())
    assert store.db.execute('SELECT count(*) FROM notices').fetchone()[0]==1


@pytest.mark.parametrize('mode',['publication_date','internal_deadline'])
def test_future_adapter_cannot_misuse_date_role_as_deadline(store,mode):
    body='参赛对象：我校本科生。校内报名截止：2026年10月20日；主办方截止：2026年11月1日。'
    notice_id=seed(store,content=body);repo=AnalysisRepository(store)
    class BadDate(RuleAnalyzer):
        def analyze(self,notice,*,reference_time):
            from cdu_radar.analysis_models import Evidence
            result=super().analyze(notice,reference_time=reference_time)
            if mode=='publication_date':quote,value=notice.published_at,notice.published_at
            else:quote,value='校内报名截止：2026年10月20日',result.internal_deadline
            evidence=tuple(e for e in result.evidence if e.field!='registration_deadline')+(Evidence('registration_deadline','fact',quote,'错误日期角色',value),)
            return replace(result,registration_deadline=value,evidence=evidence)
    result,_=analyze_one(repo,notice_id,BadDate(),ASOF)
    assert result.analysis_state=='failed' and result.registration_deadline is None and result.needs_manual_review


def test_model_name_change_does_not_reuse_wrong_model_cache(store):
    notice_id=seed(store);repo=AnalysisRepository(store)
    class FakeModel(RuleAnalyzer):
        model_name='synthetic-model-a'
        def analyze(self,notice,*,reference_time):
            return replace(super().analyze(notice,reference_time=reference_time),model_name=self.model_name)
    fake=FakeModel();_,cached=analyze_one(repo,notice_id,fake,ASOF);assert not cached
    _,cached=analyze_one(repo,notice_id,fake,ASOF);assert cached
    fake.model_name='synthetic-model-b'
    result,cached=analyze_one(repo,notice_id,fake,ASOF)
    assert not cached and result.model_name=='synthetic-model-b'


def test_cached_payload_cannot_forge_manual_approval(store):
    notice_id=seed(store);repo=AnalysisRepository(store)
    initial,_=analyze_one(repo,notice_id,reference_time=ASOF)
    payload=initial.to_dict();payload['review_status']='approved'
    with repo.db:repo.db.execute('UPDATE notice_analyses SET payload=?',(json.dumps(payload),))
    result,cached=analyze_one(repo,notice_id,reference_time=ASOF)
    assert not cached and result.review_status=='pending'
    assert repo.db.execute('SELECT count(*) FROM analysis_reviews').fetchone()[0]==0


def test_export_never_promotes_prediction_to_truth(store,tmp_path):
    seed(store);repo=AnalysisRepository(store);path=tmp_path/'labels.csv'
    export_labels(repo,path,reference_time=ASOF)
    row=worksheet(path)[0]
    assert row['label_status']=='unlabeled' and row['worth_recommending']==''
    assert row['predicted_decision']=='retain'
    assert import_labels(repo,path,labeler='test-human')=={'imported':0,'unchanged':0,'unlabeled_skipped':1}
    assert evaluate(repo,reference_time=ASOF)['precision'] is None


def test_explicit_human_import_and_idempotency(store,tmp_path):
    seed(store);repo=AnalysisRepository(store);path=tmp_path/'labels.csv';export_labels(repo,path,reference_time=ASOF)
    rows=worksheet(path);rows[0].update(label_status='human',worth_recommending='TRUE',student_opportunity='true',relevance_level='direct_ai',notes='测试人工修改')
    write_sheet(path,rows)
    assert import_labels(repo,path,labeler='test-human')['imported']==1
    assert import_labels(repo,path,labeler='test-human')['unchanged']==1
    report=evaluate(repo,reference_time=ASOF)
    assert report['valid_labels']==1 and report['confusion_matrix']['tp']==1
    assert report['precision']==1 and report['recall']==1 and not report['production_validated']
    assert store.db.execute('SELECT count(*) FROM analysis_reviews').fetchone()[0]==0


@pytest.mark.parametrize('status',['prediction','unlabeled'])
def test_prediction_and_implicit_truth_rejected(store,tmp_path,status):
    seed(store);repo=AnalysisRepository(store);path=tmp_path/'labels.csv';export_labels(repo,path,reference_time=ASOF)
    rows=worksheet(path);rows[0].update(label_status=status,worth_recommending='true');write_sheet(path,rows)
    with pytest.raises(ValueError):import_labels(repo,path,labeler='test-human')
    assert store.db.execute('SELECT count(*) FROM manual_labels').fetchone()[0]==0


def test_label_import_is_atomic_when_later_row_invalid(store,tmp_path):
    seed(store,url_id=1);seed(store,url_id=2);repo=AnalysisRepository(store);path=tmp_path/'labels.csv';export_labels(repo,path,reference_time=ASOF)
    rows=worksheet(path)
    rows[0].update(label_status='human',worth_recommending='true')
    rows[1].update(label_status='human',worth_recommending='definitely')
    write_sheet(path,rows)
    with pytest.raises(ValueError,match='row 3'):import_labels(repo,path,labeler='test-human')
    assert store.db.execute('SELECT count(*) FROM manual_labels').fetchone()[0]==0


def test_old_worksheet_rejected_after_title_change(store,tmp_path):
    seed(store);repo=AnalysisRepository(store);path=tmp_path/'labels.csv';export_labels(repo,path,reference_time=ASOF)
    rows=worksheet(path);rows[0].update(label_status='human',worth_recommending='true');write_sheet(path,rows)
    store.save_notice('computer','https://computer.cdu.edu.cn/info/1/1.htm',Article('已更新标题','2026-10-01','参赛对象：我校本科生。允许使用AI。报名截止：2026年11月1日。'))
    with pytest.raises(ValueError,match='Stale'):import_labels(repo,path,labeler='test-human')


def test_synthetic_metrics_are_explicit_and_errors_listed(store):
    ids=[seed(store,'AI竞赛报名','参赛对象：我校本科生。报名截止：2026年11月1日。',1),
         seed(store,'AI获奖喜报','我院学子荣获奖项。',2),
         seed(store,'数字艺术比赛','欢迎报名，资格尚不明确。',3)]
    repo=AnalysisRepository(store)
    with repo.db:
        for notice_id,truth in zip(ids,[False,True,False]):
            notice=repo.input(notice_id)
            repo.add_label(ManualLabel(notice_id,notice.content_hash,notice.input_hash,'synthetic','test-fixture',worth_recommending=truth,
                eligibility_status='ineligible' if notice_id==ids[0] else None,category='competition',reference_date='2026-10-13'))
    assert evaluate(repo,reference_time=ASOF)['valid_labels']==0
    report=evaluate(repo,reference_time=ASOF,include_synthetic=True)
    assert report['confusion_matrix']=={'tp':0,'fp':2,'fn':1,'tn':0}
    assert report['precision']==0 and report['recall']==0 and report['f1']==0
    assert report['synthetic_labels']==3 and report['evaluation_origin']=='synthetic_included'
    assert report['uncertain_ratio']==pytest.approx(1/3)
    assert {'false_positive','missed_opportunity','eligibility_error'}<= {t for e in report['error_samples'] for t in e['error_types']}


def test_evaluation_empty_partial_and_different_reference_date(store):
    repo=AnalysisRepository(store)
    empty=evaluate(repo,reference_time=ASOF)
    assert empty['valid_labels']==0 and empty['f1'] is None
    notice_id=seed(store);notice=repo.input(notice_id)
    with repo.db:repo.add_label(ManualLabel(notice_id,notice.content_hash,notice.input_hash,'human','test-human',notes='尚未判断'))
    assert evaluate(repo,reference_time=ASOF)['valid_labels']==0
    with repo.db:repo.add_label(ManualLabel(notice_id,notice.content_hash,notice.input_hash,'human','test-human',worth_recommending=True,reference_date='2026-10-01'))
    assert evaluate(repo,reference_time=ASOF)['reference_date_mismatches']==1


def test_intraday_label_reference_time_mismatch_is_not_scored(store):
    notice_id=seed(store);repo=AnalysisRepository(store);notice=repo.input(notice_id)
    with repo.db:repo.add_label(ManualLabel(notice_id,notice.content_hash,notice.input_hash,'human','test-human',worth_recommending=True,
        reference_date='2026-10-13',reference_time='2026-10-13T17:00:00+08:00'))
    report=evaluate(repo,reference_time=reference_datetime('2026-10-13T19:00:00+08:00'))
    assert report['valid_labels']==0 and report['reference_time_mismatches']==1
    default=evaluate(repo)
    assert default['valid_labels']==1 and default['reference_policy']=='per_label_time'


def test_raw_notice_rss_and_schema_version_preserved(store):
    notice_id=seed(store);before=dict(store.feed_rows(1)[0]);guid=ET.fromstring(build_rss(store)).findtext('./channel/item/guid')
    repo=AnalysisRepository(store);analyze_one(repo,notice_id,reference_time=ASOF)
    assert dict(store.feed_rows(1)[0])==before
    assert ET.fromstring(build_rss(store)).findtext('./channel/item/guid')==guid
    db_path=store.db.execute('PRAGMA database_list').fetchone()[2]
    with Store(db_path) as reopened:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0]==2
        assert reopened.db.execute('PRAGMA foreign_key_check').fetchall()==[]


def test_spreadsheet_injection_is_escaped(store,tmp_path):
    seed(store,title='=HYPERLINK("https://invalid.test")',content='普通测试文本。')
    repo=AnalysisRepository(store);path=tmp_path/'labels.csv';export_labels(repo,path,reference_time=ASOF)
    assert worksheet(path)[0]['title'].startswith("'=")


def test_analysis_cli_and_export_overwrite_guard(store,tmp_path,capsys):
    seed(store);db=store.db.execute('PRAGMA database_list').fetchone()[2]
    out=tmp_path/'analysis.json';labels=tmp_path/'labels.csv';report=tmp_path/'metrics.json'
    assert main(['analyze','--db',db,'--output',str(out),'--as-of','2026-10-13'])==0
    assert json.loads(out.read_text(encoding='utf-8'))['summary']['notices']==1
    assert main(['label','export','--db',db,'--output',str(labels),'--as-of','2026-10-13'])==0
    assert main(['label','export','--db',db,'--output',str(labels)])==2
    assert main(['evaluate','--db',db,'--output',str(report),'--as-of','2026-10-13'])==0
    assert json.loads(report.read_text(encoding='utf-8'))['valid_labels']==0


def test_analysis_cli_missing_db_and_database_output_refused(store,tmp_path):
    assert main(['analyze','--db',str(tmp_path/'missing.sqlite3')])==2
    db=store.db.execute('PRAGMA database_list').fetchone()[2]
    assert main(['analyze','--db',db,'--output',db])==2
