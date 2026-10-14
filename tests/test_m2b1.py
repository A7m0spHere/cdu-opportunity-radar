"""Offline regression of protocol, evidence, abstention and historical isolation."""
from dataclasses import replace
import hashlib
import json

import httpx
import pytest

from cdu_radar.analysis_models import Evidence, NoticeAnalysis
from cdu_radar.analysis_service import analyze_one
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.cli import main
from cdu_radar.evidence import locate, literal
from cdu_radar.evaluation import evaluation_layers
from cdu_radar.llm import LLMAnalyzer, LLMConfig, ChatCompletionsProvider, strict_json
from cdu_radar.llm_workflow import LLMLedger, compare
from cdu_radar.llm_diagnostics import request_stages
from cdu_radar.llm_screening import SCHEMA_VERSION
from test_llm import seed, model_payload, response, analyzer_for, ASOF, BODY, config


def screening_payload(ident):
    return {"notice_id":ident, "is_participation_opportunity":None, "category":"unknown",
            "ai_relevance":"uncertain", "ai_use_policy":"unspecified", "eligibility_status":"uncertain",
            "opportunity_status":"unknown", "evidence":[], "missing_information":["需核验报名规则"],
            "recommendation":"uncertain", "review_required":True}


@pytest.mark.parametrize("quote,status", [("参赛对象：我校本科生", "valid"),
    ("参赛对象： 我校\n本科生", "normalized_match"), ("参赛对象:我校本科生", "not_found"),
    ("学生均可参加", "not_found"), ("", "missing")])
def test_citations_only_normalize_nfc_and_whitespace(store, quote, status):
    ident = seed(store); notice = AnalysisRepository(store).input(ident)
    match = locate(notice, quote)
    assert match.status == status
    if match.source:assert literal(notice, match) == "参赛对象：我校本科生"


def test_unicode_offsets_and_cross_field_matching(store):
    ident = seed(store, "名称：Cafe\u0301 活动")
    notice = AnalysisRepository(store).input(ident)
    match = locate(notice, "Café活动")
    assert match.status == "normalized_match" and literal(notice,match) == "Cafe\u0301 活动"
    assert locate(notice, notice.title + "\n" + notice.content).status == "not_found"
    assert locate(notice, "Cafe活动").status == "not_found"
    hangul = replace(notice,content="原文：\u1100 \u1161\u11a8")
    match = locate(hangul,"각")
    assert match.status == "normalized_match" and literal(hangul,match) == "\u1100 \u1161\u11a8"


@pytest.mark.parametrize("body,field,value,quote", [
    ("不允许使用AI。参赛对象：我校本科生。", "ai_use_policy", "explicitly_allowed", "允许使用AI"),
    ("参赛者可使用AI进行图片制作。参赛对象：我校本科生。", "ai_use_policy", "explicitly_allowed", "可使用AI"),
    ("未禁止AI。参赛对象：我校本科生。", "ai_use_policy", "explicitly_allowed", "未禁止AI"),
    ("未禁止AI。参赛对象：我校本科生。", "ai_use_policy", "explicitly_prohibited", "禁止AI"),
    ("不是仅限研究生。", "eligibility_status", "ineligible", "仅限研究生"),
    ("本活动允许使用AI。", "ai_relevance", "direct_ai", "允许使用AI"),
    ("不接受我校本科生报名。", "eligibility_status", "eligible", "我校本科生"),
    ("仅限指定专业，我校本科生可报名。", "eligibility_status", "eligible", "我校本科生可报名"),
    ("参赛对象：我校2024级本科生。", "eligibility_status", "eligible", "我校2024级本科生"),
    ("面向设计专业，我校本科生可报名。", "eligibility_status", "eligible", "我校本科生可报名"),
])
def test_located_but_unsupported_v2_facts_remain_business_pending(store, config, body, field, value, quote):
    ident = seed(store,body); repo = AnalysisRepository(store)
    payload = screening_payload(ident)
    payload[field] = value; payload["evidence"] = [{"field":field,"quote":quote,"value":None}]
    analyzer = analyzer_for(repo,replace(config,schema_mode="screening_v2"),lambda _:response(payload))
    result,cached = analyze_one(repo,ident,analyzer,ASOF)
    assert result.analysis_state == "success" and result.candidate_decision == "uncertain"
    assert result.fact_status == "review_required" and result.needs_manual_review
    assert any(e.status == "unsupported" for e in result.evidence_validation)
    second,cached = analyze_one(repo,ident,analyzer,ASOF)
    assert cached and second.evidence_validation == result.evidence_validation
    stages = request_stages(repo,analyzer.version)
    assert stages["stages"]["analysis_success"]["passed"] == 1


@pytest.mark.parametrize("mode",["missing","invented","wrong_id","title_only","bad_date","unproved_open","tentative_date","whitespace_quote"])
def test_unknown_is_not_technical_failure_but_invented_quotes_are(store,config,mode):
    ident = seed(store); repo = AnalysisRepository(store)
    if mode == "title_only":
        original_input = repo.input
        repo.input = lambda i: replace(original_input(i), content="")
    payload = screening_payload(ident)
    if mode == "invented":payload["evidence"] = [{"field":"category","quote":"凭空造出的原文","value":None}]
    if mode == "wrong_id":payload["notice_id"] += 50
    if mode == "bad_date":payload["evidence"] = [{"field":"registration_deadline","quote":"报名截止：2026年11月1日","value":"2027-99-99"}]
    if mode == "unproved_open":payload["opportunity_status"] = "open"
    if mode == "tentative_date":
        original_input = repo.input
        repo.input = lambda i: replace(original_input(i),content="报名截止暂定：2026年11月1日")
        payload["evidence"] = [{"field":"registration_deadline","quote":"2026年11月1日","value":"2026-11-01"}]
    if mode == "whitespace_quote":payload["evidence"] = [{"field":"category","quote":" \n","value":None}]
    analyzer = analyzer_for(repo,replace(config,schema_mode="screening_v2"),lambda _:response(payload))
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    assert result.analysis_state == ("failed" if mode in {"invented","wrong_id"} else "success")
    assert result.candidate_decision == "uncertain" and result.registration_deadline is None
    assert result.opportunity_status == "unknown"


def test_screening_dates_and_reference_time_do_not_invent_opportunities(store,config):
    ident = seed(store); repo = AnalysisRepository(store)
    payload = screening_payload(ident)
    payload.update(is_participation_opportunity=True, category="competition",ai_relevance="direct_ai",
                   eligibility_status="eligible",opportunity_status="open",missing_information=[],recommendation="retain",review_required=False)
    payload["evidence"] = [{"field":field,"quote":quote,"value":value} for field,quote,value in [
        ("is_participation_opportunity","参赛对象：我校本科生",None), ("category","AI应用大赛报名",None),
        ("ai_relevance","AI应用大赛报名",None), ("eligibility_status","参赛对象：我校本科生",None),
        ("registration_deadline","报名截止：2026年11月1日","2026-11-01")]]
    analyzer = analyzer_for(repo,replace(config,schema_mode="screening_v2"),lambda _:response(payload))
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    assert result.analysis_state == "success" and result.fact_status == "verified"
    assert result.candidate_decision == "retain" and result.registration_deadline == "2026-11-01"
    second,cached = analyze_one(repo,ident,analyzer,"2026-11-02")
    assert cached and second.candidate_decision == "exclude"


@pytest.mark.parametrize("mode",["markdown","truncated","reasoning_only","final_with_reasoning","bad_shape","missing_quote"])
def test_diagnostics_capture_stages_without_retaining_content(store,config,mode):
    ident = seed(store); repo = AnalysisRepository(store); payload = model_payload(repo.input(ident))
    content = "```json\n{}\n```" if mode == "markdown" else " " if mode == "reasoning_only" else None
    if mode == "bad_shape":payload.pop("category")
    if mode == "missing_quote":payload["evidence"][0]["quote"] = "synthetic-invented-private-text"
    provider_response = response(payload,finish="length" if mode == "truncated" else "stop", content=content,
                                 extra_message={"reasoning_content":"synthetic-private-reasoning"})
    analyzer = analyzer_for(repo,config,lambda _:provider_response)
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    raw = repo.db.execute("SELECT payload FROM llm_request_diagnostics").fetchone()[0]
    d = json.loads(raw)
    assert "synthetic-private-reasoning" not in raw and "synthetic-invented-private-text" not in raw
    assert d["reasoning_chars"] == len("synthetic-private-reasoning")
    assert d["finish_reason"] == ("length" if mode == "truncated" else "stop")
    assert result.analysis_state == ("success" if mode == "final_with_reasoning" else "failed")
    if mode == "bad_shape":
        assert d["schema_ok"] is False
        assert d["schema_error_path"] == "$" and d["missing_schema_fields"] == ["category"]
    if mode == "missing_quote":assert d["schema_ok"] is True and d["evidence_ok"] is False
    if mode == "markdown":assert d["json_style"] == "markdown_fence" and not d["json_ok"]


def test_protocol_configuration_and_version_isolation(store,config):
    ident = seed(store); repo = AnalysisRepository(store); seen = []
    v1 = LLMAnalyzer(config)
    configured = replace(config,schema_mode="screening_v2",response_format="json_schema",thinking_mode="enabled",
                         protocol_profile="deepseek",temperature=0,output_token_parameter="max_completion_tokens")
    def handler(request):
        body = json.loads(request.content); seen.append(body)
        assert "temperature" not in body and "max_tokens" not in body
        assert body["max_completion_tokens"] == 4096
        assert "notice_id" in body["response_format"]["json_schema"]["schema"]["properties"]
        return response(screening_payload(ident))
    v2 = analyzer_for(repo,configured,handler)
    result,_ = analyze_one(repo,ident,v2,ASOF)
    assert v1.schema_version != v2.schema_version and v1.version != v2.version
    assert result.schema_version == SCHEMA_VERSION
    assert repo.cached(repo.input(ident),v1.version) is None
    assert NoticeAnalysis.from_dict(json.loads(json.dumps(result.to_dict()))) == result
    configured_env = LLMConfig.from_env({"CDU_LLM_BASE_URL":config.base_url,"CDU_LLM_MODEL":config.model,
                                        "CDU_LLM_STOP_AFTER_FORMAT_FAILURES":"1"})
    assert configured_env.stop_after_format_failures == 1


def test_business_metrics_never_credit_fallback_or_unknown_as_model_tp():
    layers = evaluation_layers([
        {"notice_id":1,"truth":True,"decision":"uncertain","state":"failed"},
        {"notice_id":2,"truth":True,"decision":"uncertain","state":"success"},
        {"notice_id":3,"truth":False,"decision":"exclude","state":"success"},
        {"notice_id":4,"truth":True,"decision":None,"state":"not_analyzed"}])
    assert layers["business"]["comparable_labels"] == 1
    assert layers["business"]["comparable_metrics"]["recall"] is None
    assert layers["business"]["comparable_metrics"]["confusion_matrix"]["tp"] == 0
    assert layers["system"]["final_candidate_recall"] == 1
    assert layers["system"]["pending_due_to_model_failure"] == 1


def test_batch_stops_after_two_format_errors_without_spending_the_third(store,config):
    from cdu_radar.llm_workflow import run_batch
    ids = [seed(store,ident=i) for i in [1,2,3]]; repo = AnalysisRepository(store)
    analyzer = analyzer_for(repo,config,lambda _:response({},content="broken JSON"))
    report = run_batch(repo,analyzer,ids,reference_time=ASOF)
    assert report["processed"] == 2 and report["unprocessed_ids"] == [ids[2]]
    assert report["stop_reason"] == "consecutive_format_failures"
    assert repo.db.execute("SELECT count(*) FROM llm_requests").fetchone()[0] == 2


def test_old_cache_remains_readable_when_new_evidence_guard_rejects_it(store,config):
    ident = seed(store); repo = AnalysisRepository(store)
    analyzer = analyzer_for(repo,config,lambda _:response(model_payload(repo.input(ident))))
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    # Remove new optional fields to exercise reading the original M2-B serialized shape.
    old = json.loads(json.dumps(result.to_dict()))
    for key in ["schema_version","fact_status","evidence_validation"]:old.pop(key)
    restored = NoticeAnalysis.from_dict(old)
    assert restored.schema_version is None and restored.fact_status == "not_checked"
    assert restored.evidence_validation == ()


def test_v1_normalized_quotes_pass_all_validation_stages(store,config):
    ident = seed(store); repo = AnalysisRepository(store); payload = model_payload(repo.input(ident))
    for e in payload["evidence"]:
        if e["quote"]:e["quote"] = " \n".join(e["quote"])
    analyzer = analyzer_for(repo,config,lambda _:response(payload))
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    assert result.analysis_state == "success"
    assert any(e.location_status == "normalized_match" for e in result.evidence_validation)


def test_history_cli_needs_no_provider_config_and_preserves_database(store,config,monkeypatch,tmp_path):
    ident = seed(store); repo = AnalysisRepository(store)
    analyzer = analyzer_for(repo,config,lambda _:response(model_payload(repo.input(ident))))
    result,_ = analyze_one(repo,ident,analyzer,ASOF)
    path = store.db.execute("PRAGMA database_list").fetchone()[2]
    from pathlib import Path
    before = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    monkeypatch.delenv("CDU_LLM_BASE_URL",raising=False); monkeypatch.delenv("CDU_LLM_MODEL",raising=False)
    out = tmp_path/"history.json"
    assert main(["llm","history","--db",path,"--analysis-version",analyzer.version,"--output",str(out)]) == 0
    report = json.loads(out.read_text("utf-8"))
    assert report["archive_only"] and not report["revalidated"] and report["api_requests"] == 0
    assert report["analyses"][0] == json.loads(json.dumps(result.to_dict()))
    assert hashlib.sha256(Path(path).read_bytes()).hexdigest() == before


@pytest.mark.parametrize("text",['{"x":NaN}','{"x":Infinity}','{"x":1,"x":2}'])
def test_strict_json_rejects_nonstandard_and_duplicate_values(text):
    with pytest.raises(ValueError):strict_json(text)
