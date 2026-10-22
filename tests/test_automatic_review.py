from copy import deepcopy
from dataclasses import replace
import json

import pytest

from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.async_queue import JobQueue
from cdu_radar.automatic_review import AutomaticReviewAnalyzer
from cdu_radar.featured import FeaturedPolicy
from cdu_radar.llm import LLMConfig
from cdu_radar.review_api import review, validate_public_v4
from cdu_radar.quality_benchmark import (export_package, load_package, implementation_version,
                                        rebase_labels, import_labels)
from cdu_radar.analysis_models import fingerprint
from test_auto_publish import auto_service, NOW, add
from test_featured import raw_path
from test_quality_benchmark import label
from test_rss_service import request, loopback_only

QUOTE='面向在校本科生开放跨学科项目实践，学生可以通过项目平台提交作品与申请。'


def analyzer():
    return AutomaticReviewAnalyzer(LLMConfig('https://provider.example.test/v1','test-model',retries=0,
        response_format='json_object',analysis_mode='automatic_review_v3'),mock=True)


def payload(quote=QUOTE):
    return dict(classification=dict(type='research',domains=['research'],topics=[],reason='研究实践招募',
        evidence=[dict(field='type',value='research',quote=quote),dict(field='domain',value='research',quote=quote)]),
        importance=dict(level='high',kind='inference',reason='有明确学生实践价值',quotes=[quote]),
        selection=dict(decision='selected',reason='学生可参与的实践项目',criteria=['participation'],quotes=[quote]),
        student=dict(state='relevant',reason_code='student_opportunity',reason='面向本科生',quotes=[quote],summary_quotes=[quote]),
        action=dict(state='yes',reason='有提交作品与申请的具体事项',quotes=[quote]))


def setup(store,tmp_path):
    add(store,title='跨学科项目实践计划',body=QUOTE+' 本条仅用于合成软件验证，完整信息以官方原文为准。')
    a=analyzer(); n=AnalysisRepository(store).input(1); supplied,_=a.prepare(n)
    p=payload(); checked=a.validate(p,n,supplied,NOW)
    return a,n,supplied,p,checked


def test_semantic_selection_is_not_a_second_keyword_classifier(store,tmp_path):
    a,n,s,p,result=setup(store,tmp_path)
    assert result['classification_valid'] and result['student_selection_valid'] and result['action_valid']
    assert result['action_basis'] is True
    assert FeaturedPolicy().decide(n,result,'succeeded')['decision']=='selected'
    projected=review({},dict(title=n.title,content=n.content),result,'succeeded')
    assert projected['undergraduate']['state']=='relevant' and projected['undergraduate']['summary']==QUOTE
    assert result['publication_permission'] is False


@pytest.mark.parametrize('field', ['student','action','selection'])
def test_fabricated_selection_evidence_cannot_become_public_decisions(store,tmp_path,field):
    a,n,s,p,result=setup(store,tmp_path)
    p[field]['quotes']=['这段话从未出现在原文中。']
    checked=a.validate(p,n,s,NOW)
    key={'student':'student_selection_valid','action':'action_valid','selection':'featured_selection_valid'}[field]
    assert checked[key] is False and checked['state']=='partial'
    assert checked['classification_valid']
    if field=='selection':assert FeaturedPolicy().decide(n,checked,'partial')['decision']=='pending'


def test_teacher_only_scope_cannot_become_undergraduate_eligibility(store,tmp_path):
    a,n,s,p,_=setup(store,tmp_path)
    n=replace(n,title='教师教学创新大赛工作方案');s,_=a.prepare(n)
    checked=a.validate(p,n,s,NOW)
    assert checked['student_selection_valid'] is False
    assert review({},dict(title=n.title,content=n.content),checked,'partial')['undergraduate']['state']=='excluded'


def test_valid_negative_and_unknown_selections_stay_separate(store,tmp_path):
    a,n,s,p,_=setup(store,tmp_path)
    p['selection'].update(decision='not_selected',criteria=[])
    p['action']['state']='no'
    checked=a.validate(p,n,s,NOW)
    assert FeaturedPolicy().decide(n,checked,checked['state'])['decision']=='not_selected'
    assert checked['action_basis'] is False
    p['selection'].update(decision='uncertain',quotes=[])
    checked=a.validate(p,n,s,NOW)
    assert FeaturedPolicy().decide(n,checked,checked['state'])['decision']=='pending'


def test_multiline_original_quote_stays_extractive_not_an_invalid_digest(store,tmp_path):
    a,n,s,p,_=setup(store,tmp_path)
    text='参赛人员\n我院全体在读学生'
    n=replace(n,content=n.content+'\n'+text);s,_=a.prepare(n)
    p['student'].update(quotes=[text],summary_quotes=[text])
    checked=a.validate(p,n,s,NOW)
    assert checked['student_selection_valid'] and checked['student_selection']['summary']==text


def test_bounded_evidence_keeps_all_digest_proof(store,tmp_path):
    a,n,s,p,_=setup(store,tmp_path)
    spans=['本科生可以了解跨学科实践的成果。','学生可以从项目作品中学习研究方法。','学生可以在作品平台交流实践经验。',
           '本项目提供本科生实践参考信息。','学生可以了解具体作品提交安排。','本活动面向学生提供实践过程资料。']
    n=replace(n,content=n.content+'\n'+'\n'.join(spans));s,_=a.prepare(n)
    p['student'].update(quotes=spans[:3],summary_quotes=spans[3:])
    checked=a.validate(p,n,s,NOW)
    assert checked['student_selection_valid']
    assert len(checked['student_selection']['evidence'])==4
    assert all(q in checked['student_selection']['evidence'] for q in spans[3:])


def test_invalid_classification_quote_does_not_discard_other_valid_dimensions(store,tmp_path):
    a,n,s,p,_=setup(store,tmp_path)
    p['classification']['evidence'][0]['quote']='原文中不存在的分类依据。'
    checked=a.validate(p,n,s,NOW)
    assert checked['classification_valid'] is False and checked['state']=='partial'
    assert checked['student_selection_valid'] and checked['action_valid']
    assert checked['featured_selection_valid']
    assert 'classification:invalid_evidence_or_bounds' in checked['issues']


def test_cached_semantic_result_reaches_real_http_without_model_calls(auto_service,tmp_path,monkeypatch):
    store,c,server,*_=auto_service;a,n,s,p,checked=setup(store,tmp_path)
    jobs=tmp_path/'v3.sqlite3'
    with JobQueue(jobs,raw_path(store)) as q:
        q.scan(a,notice_ids=[1]);q.db.execute('UPDATE jobs SET state=?,result=?',
            (checked['state'],json.dumps(checked,ensure_ascii=False)))
    c.data_mode='offline_replay';c.analysis_state=jobs;c.analysis_analyzer=a
    assert c.refresh()
    monkeypatch.setattr(a.provider,'complete',lambda *a,**k:pytest.fail('HTTP must not call a model'))
    status,_,body=request(server,'/api/notices/v4?audience=undergraduate')
    assert status==200
    result=json.loads(body);validate_public_v4(result)
    assert any(i['source_url']==n.canonical_url and i['classification']['type']=='research' for i in result['items'])
    assert request(server,'/rss/v2/featured.xml?audience=undergraduate&type=research')[0]==200


def test_human_answers_survive_prediction_version_refresh(auto_service,tmp_path):
    raw=raw_path(auto_service[0]);old=tmp_path/'old';new=tmp_path/'new'
    export_package(raw,old,reference_time=NOW.isoformat())
    csv_path=label(old)
    manifest=json.loads((old/'predictions.json').read_text('utf-8'))
    manifest['implementation_version']='previous-implementation'
    manifest['benchmark_id']='quality-benchmark:'+fingerprint({k:v for k,v in manifest.items() if k!='benchmark_id'})
    (old/'predictions.json').write_text(json.dumps(manifest),encoding='utf-8')
    with pytest.raises(ValueError):load_package(old)
    assert load_package(old,require_current=False)[0]['records']
    assert import_labels(old,csv_path,tmp_path/'old-labels.sqlite3',labeler='synthetic-test')['imported']>0
    export_package(raw,new,reference_time=NOW.isoformat())
    output=tmp_path/'rebased.csv'
    assert rebase_labels(old,new,csv_path,output)['human_answers_changed']==0
    assert import_labels(new,output,tmp_path/'new-labels.sqlite3',labeler='synthetic-test')['imported']>0


def test_benchmark_uses_explicit_model_selection_instead_of_importance_only(auto_service,tmp_path):
    store=auto_service[0];a,n,s,p,_=setup(store,tmp_path)
    p['selection'].update(decision='not_selected',criteria=[])
    checked=a.validate(p,n,s,NOW);jobs=tmp_path/'selection-metric.sqlite3'
    with JobQueue(jobs,raw_path(store)) as q:
        q.scan(a,notice_ids=[1]);q.db.execute('UPDATE jobs SET state=?,result=?',
            (checked['state'],json.dumps(checked,ensure_ascii=False)))
    directory=tmp_path/'explicit-metric'
    export_package(raw_path(store),directory,reference_time=NOW.isoformat(),state_db=jobs,analyzer=a)
    _,manifest=load_package(directory)
    prediction=next(p for p in manifest['predictions'] if p['sample_id']=='1')
    assert checked['importance']['level']=='high' and prediction['model']['featured'] is False
