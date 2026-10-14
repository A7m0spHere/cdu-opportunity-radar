"""Offline field isolation, scope and date regression; no live model calls."""
from dataclasses import replace
import json
from pathlib import Path
from xml.etree import ElementTree as ET

import pytest

from cdu_radar.analysis_models import NoticeAnalysis
from cdu_radar.analysis_service import analyze_one
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.llm_semantics import supports_eligibility
from cdu_radar.llm_screening import VALIDATION_VERSION
from cdu_radar.rss import build_rss
from test_llm import seed, response, analyzer_for, ASOF, config
from test_m2b1 import screening_payload


def complete_payload(ident):
    p = screening_payload(ident)
    p.update(is_participation_opportunity=True,category="competition",ai_relevance="direct_ai",
        eligibility_status="eligible",opportunity_status="open",recommendation="retain",review_required=False,missing_information=[])
    p["evidence"] = [{"field":f,"quote":q,"value":None} for f,q in [
        ("is_participation_opportunity","参赛对象：我校本科生"),
        ("eligibility_status","参赛对象：我校本科生"),
        ("category","AI应用大赛报名"),("ai_relevance","AI应用大赛报名")]]
    return p


def run(store,config,body,payload=None,asof=ASOF):
    existing=store.db.execute('SELECT id,content FROM notices WHERE canonical_url=?',('https://computer.cdu.edu.cn/info/1/1.htm',)).fetchone()
    if existing:
        assert existing['content']==body
        ident=existing['id']
    else:ident=seed(store,body)
    repo=AnalysisRepository(store)
    p=payload or complete_payload(ident)
    analyzer=analyzer_for(repo,replace(config,schema_mode="screening_v2"),lambda _:response(p))
    result,cached=analyze_one(repo,ident,analyzer,asof)
    return result,repo,analyzer


def fields(result):return {f.field:f for f in result.field_validation}


@pytest.mark.parametrize("phrase",["本科生","本、专科学生","本专科学生","全日制普通本科生","在校大学生","普通高等学校在校生"])
@pytest.mark.parametrize("scope",["我校","全国"])
def test_student_expressions_with_explicit_admission_scope(store,phrase,scope):
    ident=seed(store,f"参赛对象：{scope}{phrase}。")
    n=AnalysisRepository(store).input(ident)
    assert supports_eligibility("eligible",n.content,n)


@pytest.mark.parametrize("restriction",[
    "仅限清华大学本科生参加。", "参赛对象：四川大学在校大学生。", "仅限指定院校参加。",
    "仅限计算机专业。", "面向四川省高校在校生。", "参赛对象：省内本科生。",
    "参赛对象：我校2024级本科生。", "面向大三学生。", "必须通过面试选拔。",
    "须具备C语言编程基础。", "报名对象须获得学院推荐。", "仅限本院本科生。",
    "仅限非成都大学本科生。", "报名对象：外校在校大学生。", "成都大学学生除外。",
    "户籍须在四川省。", "学校所在地须在北京市。",
    "参赛对象：北京大学在校大学生。", "参赛院校须为211高校。", "仅限四川高校参加。",
])
def test_student_words_do_not_erase_restrictions(store,restriction):
    ident=seed(store,"参赛对象：我校本、专科学生。"+restriction)
    n=AnalysisRepository(store).input(ident)
    assert not supports_eligibility("eligible","我校本、专科学生",n)


@pytest.mark.parametrize("body",["我校本科生荣获竞赛奖项。","若允许我校本科生报名，请咨询。","是否面向在校大学生？","不接受我校本科生报名。","并非面向我校本科生。"])
def test_ambiguous_or_negative_student_mentions(store,body):
    ident=seed(store,body);n=AnalysisRepository(store).input(ident)
    assert not supports_eligibility("eligible",body,n)


@pytest.mark.parametrize("body,value,supported",[
    ("仅限清华大学本科生报名。","ineligible",True),
    ("仅限成都大学本科生报名。","eligible",True),
    ("不是仅限清华大学本科生报名。","ineligible",False),
    ("参赛对象：全国本专科学生。仅限清华大学本科生报名。","ineligible",False),
    ("仅限研究生报名。","ineligible",True),
])
def test_explicit_institution_exclusion_and_conflicts(store,body,value,supported):
    ident=seed(store,body);n=AnalysisRepository(store).input(ident)
    assert supports_eligibility(value,body,n)==supported


def test_ai_competition_known_but_open_without_date_is_not_verified(store,config):
    result,_,_=run(store,config,"参赛对象：我校本科生。")
    assert result.analysis_state=="success" and result.category=="competition" and result.relevance_level=="direct_ai"
    assert result.opportunity_status=="unknown" and result.candidate_decision=="uncertain"
    assert result.review_status=="pending" and result.fact_status=="review_required"
    audit=fields(result)
    assert audit["category"].status==audit["ai_relevance"].status=="verified"
    assert audit["opportunity_status"].proposed_value=="open" and audit["opportunity_status"].trusted_value=="unknown"
    assert "$.opportunity_status" in audit["opportunity_status"].error_paths


def test_category_independent_of_participation_citation(store,config):
    ident=seed(store,"将举办AI应用大赛。")
    p=complete_payload(ident)
    p["evidence"]=[{"field":f,"quote":q,"value":None} for f,q in [
        ("is_participation_opportunity","将举办AI应用大赛"),("category","AI应用大赛报名"),("ai_relevance","AI应用大赛报名")]]
    result,_,_=run(store,config,"将举办AI应用大赛。",p)
    assert result.student_opportunity is None and result.category=="competition"
    assert fields(result)["category"].status=="verified" and result.candidate_decision=="uncertain"


@pytest.mark.parametrize("bad_quote,bad_value,reason",[
    ("比赛时间：2026年11月2日","2026-11-02","wrong_date_role"),
    ("校内报名截止：2026年10月20日","2026-99-99","invalid_date"),
    ("校内报名截止：11月1日","2026-11-01","value_not_in_quote_or_year_missing"),
    ("发布日期：2026年10月14日","2026-10-14","metadata_or_tentative_date"),
])
def test_bad_internal_date_preserves_independent_registration_deadline(store,config,bad_quote,bad_value,reason):
    body="参赛对象：我校本科生。报名截止：2026年12月1日。"+bad_quote+"。"
    ident=seed(store,body);p=complete_payload(ident)
    p["evidence"] += [{"field":"registration_deadline","quote":"报名截止：2026年12月1日","value":"2026-12-01"},
        {"field":"internal_deadline","quote":bad_quote,"value":bad_value}]
    result,_,_=run(store,config,body,p)
    assert result.analysis_state=="success" and result.registration_deadline=="2026-12-01"
    assert result.internal_deadline is None and result.category=="competition"
    assert fields(result)["registration_deadline"].status=="verified"
    assert reason in fields(result)["internal_deadline"].reason
    assert result.candidate_decision=="uncertain" and result.review_status=="pending"


@pytest.mark.parametrize("include_second",[False,True])
def test_same_role_conflicting_deadlines_not_arbitrarily_selected(store,config,include_second):
    body="参赛对象：我校本科生。A赛道报名截止：2026年11月1日；B赛道报名截止：2026年12月1日。"
    ident=seed(store,body);p=complete_payload(ident)
    p["evidence"].append({"field":"registration_deadline","quote":"A赛道报名截止：2026年11月1日","value":"2026-11-01"})
    if include_second:p["evidence"].append({"field":"registration_deadline","quote":"B赛道报名截止：2026年12月1日","value":"2026-12-01"})
    result,_,_=run(store,config,body,p)
    assert result.registration_deadline is None and result.opportunity_status=="unknown"
    assert "conflicting_source_dates" in fields(result)["registration_deadline"].reason or "multiple_dates" in fields(result)["registration_deadline"].reason
    assert result.category=="competition" and result.review_status=="pending"


def test_internal_and_external_deadlines_separate_from_event_date(store,config):
    body="参赛对象：我校本科生。校内报名截止：2026年10月13日；主办方截止：2026年11月1日。比赛时间：2026年12月1日。"
    ident=seed(store,body);p=complete_payload(ident)
    p["evidence"] += [{"field":f,"quote":q,"value":v} for f,q,v in [
        ("internal_deadline","校内报名截止：2026年10月13日","2026-10-13"),
        ("registration_deadline","主办方截止：2026年11月1日","2026-11-01")]]
    result,_,_=run(store,config,body,p)
    assert result.registration_deadline=="2026-11-01" and result.internal_deadline=="2026-10-13"
    assert result.opportunity_status=="unknown" and result.candidate_decision=="uncertain"


@pytest.mark.parametrize("reference,status",[("2026-10-13T15:59:59+00:00","open"),("2026-10-13T16:00:01+00:00","closed")])
def test_shanghai_deadline_boundary_even_with_utc_reference(store,config,reference,status):
    body="参赛对象：我校本科生。报名截止：2026年10月13日。"
    ident=seed(store,body);p=complete_payload(ident);p["opportunity_status"]=status
    p["evidence"].append({"field":"registration_deadline","quote":"报名截止：2026年10月13日","value":"2026-10-13"})
    result,_,_=run(store,config,body,p,reference)
    assert result.opportunity_status==status and result.reference_time.endswith("+08:00")


def test_noncritical_unlocatable_date_keeps_other_fields_and_reports_location_failure(store,config):
    ident=seed(store,"参赛对象：我校本科生。")
    p=complete_payload(ident)
    p["evidence"].append({"field":"internal_deadline","quote":"不存在的日期原文","value":"2026-11-01"})
    result,repo,analyzer=run(store,config,"参赛对象：我校本科生。",p)
    assert result.analysis_state=="success" and result.internal_deadline is None and result.category=="competition"
    assert "$.evidence[4].quote" in fields(result)["internal_deadline"].error_paths
    assert any(c.location_status=="not_found" for c in result.evidence_validation)
    diagnostic=json.loads(repo.db.execute('SELECT payload FROM llm_request_diagnostics').fetchone()[0])
    assert diagnostic["evidence_ok"] is False and diagnostic["analysis_ok"] is True
    again,cached=analyze_one(repo,ident,analyzer,ASOF)
    assert cached and again.field_validation==result.field_validation


def test_new_field_contract_roundtrip_and_historical_shape_defaults(store,config):
    result,_,_=run(store,config,"参赛对象：我校本科生。")
    shape=json.loads(json.dumps(result.to_dict()))
    assert NoticeAnalysis.from_dict(shape)==result and result.validation_version==VALIDATION_VERSION
    shape.pop('field_validation');shape.pop('validation_version')
    old=NoticeAnalysis.from_dict(shape)
    assert old.field_validation==() and old.validation_version is None


def test_raw_data_labels_reviews_rss_and_old_cache_unchanged(store,config):
    ident=seed(store,"参赛对象：我校本科生。")
    repo=AnalysisRepository(store)
    old_analyzer=analyzer_for(repo,replace(config,schema_mode="screening_v2"),lambda _:response(screening_payload(ident)))
    old,_=analyze_one(repo,ident,old_analyzer,ASOF)
    legacy=replace(old,analyzer_version='llm-v2:'+('a'*24));repo.save(legacy)
    original=repo.db.execute('SELECT payload FROM notice_analyses WHERE analyzer_version=?',(legacy.analyzer_version,)).fetchone()[0]
    repo.review(ident,'pending','offline fixture','synthetic reviewer')
    tables=['notices','manual_labels','analysis_reviews','notice_links']
    before={t:repo.db.execute('SELECT * FROM '+t).fetchall() for t in tables}
    feed_before=[ET.tostring(item) for item in ET.fromstring(build_rss(store)).findall('./channel/item')]
    result,_,analyzer=run(store,config,"参赛对象：我校本科生。")
    assert result.review_status=='pending' and result.candidate_decision=='uncertain'
    assert analyzer.version != legacy.analyzer_version
    assert repo.db.execute('SELECT payload FROM notice_analyses WHERE analyzer_version=?',(legacy.analyzer_version,)).fetchone()[0]==original
    assert all(repo.db.execute('SELECT * FROM '+t).fetchall()==v for t,v in before.items())
    assert [ET.tostring(item) for item in ET.fromstring(build_rss(store)).findall('./channel/item')]==feed_before


def test_partial_id34_fixture_is_not_a_complete_model_replay(store,config):
    fixture=json.loads((Path(__file__).parent/'fixtures/m2b3/id34_observed_facts.json').read_text('utf-8'))
    assert fixture['full_response_available'] is False and fixture['source']=='synthetic_adaptation_of_ID34_observed_fields'
    ident=seed(store,fixture['body'])
    p=screening_payload(ident)
    p.update(category='competition',eligibility_status='eligible',opportunity_status='open')
    p['evidence']=fixture['evidence']
    result,_,_=run(store,config,fixture['body'],p)
    assert result.category=='competition' and result.opportunity_status=='unknown'
    assert result.eligibility_status=='uncertain' and 'additional_conditions' in fields(result)['eligibility_status'].reason
    assert result.review_status=='pending' and result.candidate_decision=='uncertain'


def test_missing_year_in_uncited_second_track_blocks_openness(store,config):
    body='参赛对象：我校本科生。A赛道报名截止：2026年11月1日；B赛道报名截止：12月1日。'
    ident=seed(store,body);p=complete_payload(ident)
    p['evidence'].append({'field':'registration_deadline','quote':'A赛道报名截止：2026年11月1日','value':'2026-11-01'})
    result,_,_=run(store,config,body,p)
    assert result.registration_deadline is None and result.opportunity_status=='unknown'


def test_explicit_registration_range_has_distinct_endpoints(store,config):
    body='参赛对象：我校本科生。报名时间：2026年11月1日至12月1日。'
    ident=seed(store,body);p=complete_payload(ident);p['opportunity_status']='upcoming'
    p['evidence'] += [{'field':f,'quote':'报名时间：2026年11月1日至12月1日','value':v} for f,v in [
        ('registration_start_date','2026-11-01'),('registration_deadline','2026-12-01')]]
    result,_,_=run(store,config,body,p)
    assert result.registration_start_date=='2026-11-01' and result.registration_deadline=='2026-12-01'
    assert result.opportunity_status=='upcoming' and result.fact_status=='verified'
    assert result.review_status=='pending'


def test_start_after_deadline_is_explicit_field_conflict(store,config):
    body='参赛对象：我校本科生。报名开始：2026年12月1日。报名截止：2026年11月1日。'
    ident=seed(store,body);p=complete_payload(ident)
    p['evidence'] += [{'field':f,'quote':q,'value':v} for f,q,v in [
        ('registration_start_date','报名开始：2026年12月1日','2026-12-01'),('registration_deadline','报名截止：2026年11月1日','2026-11-01')]]
    result,_,_=run(store,config,body,p)
    assert result.registration_deadline=='2026-11-01' and result.registration_start_date is None
    assert result.opportunity_status=='unknown' and 'starts_after_deadline' in fields(result)['registration_start_date'].reason


def test_negated_closed_citation_does_not_become_explicit_closed(store,config):
    body='参赛对象：我校本科生。并非报名已结束。'
    ident=seed(store,body);p=complete_payload(ident);p['opportunity_status']='closed'
    p['evidence'].append({'field':'opportunity_status','quote':'报名已结束','value':None})
    result,_,_=run(store,config,body,p)
    assert result.opportunity_status=='unknown' and result.status_basis=='unknown'


def test_forged_field_audit_is_rejected_from_cache(store,config):
    result,repo,analyzer=run(store,config,'参赛对象：我校本科生。')
    bad=replace(result,field_validation=tuple(replace(f,trusted_value='open') if f.field=='opportunity_status' else f for f in result.field_validation))
    with pytest.raises(ValueError,match='Field audit'):analyzer.validate_cached(repo.input(result.notice_id),bad)


def test_wrong_ai_permission_preserves_category_but_blocks_recommendation(store,config):
    body='参赛对象：我校本科生。参赛者可使用AI进行图片制作。报名截止：2026年11月1日。'
    ident=seed(store,body);p=complete_payload(ident);p['ai_use_policy']='explicitly_allowed'
    p['evidence'] += [{'field':'ai_use_policy','quote':'可使用AI','value':None},
        {'field':'registration_deadline','quote':'报名截止：2026年11月1日','value':'2026-11-01'}]
    result,_,_=run(store,config,body,p)
    assert result.category=='competition' and result.relevance_level=='direct_ai'
    assert result.ai_use_policy=='unspecified' and fields(result)['ai_use_policy'].status=='unsupported'
    assert result.candidate_decision=='uncertain' and result.review_status=='pending'


def test_bad_optional_date_does_not_erase_explicit_closed_fact(store,config):
    body='参赛对象：我校本科生。报名已结束。校内报名截止：11月1日。'
    ident=seed(store,body);p=complete_payload(ident);p['opportunity_status']='closed'
    p['evidence'] += [{'field':'opportunity_status','quote':'报名已结束','value':None},
        {'field':'internal_deadline','quote':'校内报名截止：11月1日','value':'2026-11-01'}]
    result,_,_=run(store,config,body,p)
    assert result.opportunity_status=='closed' and result.internal_deadline is None
    assert result.review_status=='pending' and result.candidate_decision=='uncertain'


@pytest.mark.parametrize('phrase',['本科生','本、专科学生','本专科学生'])
def test_eligibility_heading_on_previous_line_is_bounded_context(store,phrase):
    body=f'一、报名对象\n\n我校全体在校{phrase}。'
    ident=seed(store,body);n=AnalysisRepository(store).input(ident)
    assert supports_eligibility('eligible',f'我校全体在校{phrase}',n)


def test_non_admission_heading_does_not_prove_eligibility(store):
    ident=seed(store,'一、获奖对象\n我校全体在校本科生。');n=AnalysisRepository(store).input(ident)
    assert not supports_eligibility('eligible','我校全体在校本科生',n)


def test_model_missing_information_not_confused_with_validator_field_error(store,config):
    body='参赛对象：我校本科生。报名截止：2026年11月1日。'
    ident=seed(store,body);p=complete_payload(ident)
    p['missing_information']=['category: model-reported concern']
    p['evidence'].append({'field':'registration_deadline','quote':'报名截止：2026年11月1日','value':'2026-11-01'})
    result,_,_=run(store,config,body,p)
    assert fields(result)['category'].status=='verified'
    assert any(e.value=='model_missing_information' and e.reason==p['missing_information'][0] for e in result.evidence)
    assert result.review_status=='pending' and result.candidate_decision=='uncertain'
