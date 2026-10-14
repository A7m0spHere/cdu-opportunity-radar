"""Mock-only proof of the two gates, bounded spending and redacted diagnostics."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from cdu_radar.analysis_models import ManualLabel
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.llm import external_text
from cdu_radar.llm_screening import coerce_screening
from scripts.m2b2_probe import ORDER, config_for, execute_samples, main, parameters, prepare_plan
from test_llm import seed, analyzer_for, response, ASOF, config
from test_m2b1 import screening_payload


def prepare_repository(store):
    for ident in range(1,37):seed(store,ident=ident)
    repo = AnalysisRepository(store)
    with repo.db:
        for ident in ORDER:
            n = repo.input(ident)
            repo.add_label(ManualLabel(ident,n.content_hash,n.input_hash,"human","synthetic-test-user",
                worth_recommending=ident!=1,reference_time="2026-10-14T00:00:00+08:00",notes="human-private-note"))
    return repo


def test_dry_run_is_network_free_label_blind_and_preserves_database(store,config,tmp_path,monkeypatch):
    repo = prepare_repository(store)
    path = Path(repo.db.execute("PRAGMA database_list").fetchone()[2]); before = hashlib.sha256(path.read_bytes()).hexdigest()
    monkeypatch.delenv("CDU_LLM_API_KEY",raising=False)
    target = tmp_path/"plan.json"
    assert main(["--dry-run","--db",str(path),"--plan",str(target),"--base-url",config.base_url]) == 0
    plan = json.loads(target.read_text("utf-8"))
    assert plan["api_requests"]==0 and plan["frozen"]["request_order"]==ORDER
    assert plan["frozen"]["config"]["retries"]==0 and plan["frozen"]["config"]["max_output_tokens"]==4096
    assert all(s["request_payload"]["response_format"]=={"type":"json_object"} for s in plan["samples"])
    messages = json.dumps([s["request_payload"]["messages"] for s in plan["samples"]])
    assert "human-private-note" not in messages and "synthetic-test-user" not in messages and "worth_recommending" not in messages
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before
    with pytest.raises(ValueError,match="Approved plan differs"):
        main(["--send","--db",str(path),"--plan",str(target),"--base-url",config.base_url])
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before


@pytest.mark.parametrize("failure",["http","http_param","timeout","api_json","json","schema","quote"])
def test_first_technical_failure_stops_every_remaining_sample_and_has_safe_evidence(store,config,failure):
    repo = prepare_repository(store); seen = []
    fixed = config_for(config.base_url,config.key_env)
    def handler(request):
        sent = json.loads(request.content); seen.append(sent)
        assert {k:v for k,v in sent.items() if k!="messages"}==parameters(fixed)
        payload = screening_payload(34)
        if failure=="http":return httpx.Response(500,text="private-service-error")
        if failure=="http_param":return httpx.Response(400,json={"error":{"param":"response_format","message":"private-service-error"},"usage":{"prompt_tokens":10,"completion_tokens":0,"total_tokens":10}})
        if failure=="timeout":raise httpx.ReadTimeout("private-service-error",request=request)
        if failure=="api_json":return httpx.Response(200,text='{"error":"示例甲 fake-sensitive-token" BROKEN')
        if failure=="json":return response({},content='{"quote":"示例甲 fake-sensitive-token qq12345678" BROKEN')
        if failure=="schema":payload["category"] = {"unknown-personal-key":"示例甲 fake-sensitive-token"}
        if failure=="quote":payload["evidence"] = [{"field":"category","quote":"示例甲 fake-sensitive-token 不在输入中","value":None}]
        return response(payload)
    analyzer = analyzer_for(repo,fixed,handler)
    report = execute_samples(repo,analyzer,ORDER)
    assert len(seen)==1 and report["api_requests"]==1 and report["stopped"]
    assert report["unprocessed_ids"]==[1,19,20,9]
    record = report["records"][0]
    assert not record["technical_success"] and record["business_correct_against_human"] is None
    diagnostic = record["diagnostic"]
    evidence = diagnostic["failure_evidence"]
    serialized = json.dumps(diagnostic,ensure_ascii=False)
    for private in ["示例甲","fake-sensitive-token","12345678","private-service-error","unknown-personal-key"]:
        assert private not in serialized
    assert len(evidence.get("structural_excerpt",""))<=384
    assert len(evidence.get("redacted_payload_excerpt",""))<=4096
    if failure=="schema":assert evidence["field_path"]=="$.category"
    if failure=="http_param":
        assert diagnostic["provider_error_param"]=="response_format" and evidence["field_path"]=="$.error.param"
        assert record["usage"]["total_tokens"]==10
    if failure=="api_json":
        assert diagnostic["validation_stage"]=="api_envelope" and evidence["excerpt_source"]=="api_envelope"
        assert diagnostic["api_json_error_line"]==1
    if failure=="quote":
        assert evidence["field_path"]=="$.evidence[0].quote"
        # Replay the same location stage with the explicitly redacted projection.
        projection = json.loads(evidence["redacted_payload_excerpt"])
        notice = repo.input(34); supplied,_ = analyzer.prepare(notice)
        with pytest.raises(ValueError,match="Unlocatable evidence"):
            coerce_screening(projection,notice,supplied,analyzer,ASOF)


def test_five_technical_passes_can_all_be_business_abstentions(store,config):
    repo = prepare_repository(store)
    analyzer = analyzer_for(repo,config_for(config.base_url,config.key_env),
        lambda request:response(screening_payload(json.loads(json.loads(request.content)["messages"][-1]["content"])["untrusted_notice"]["notice_id"])))
    report = execute_samples(repo,analyzer,ORDER)
    assert report["api_requests"]==5 and not report["stopped"] and not report["unprocessed_ids"]
    assert all(r["technical_success"] and r["business_unknown"] and r["business_correct_against_human"] is None for r in report["records"])
    assert [r["gate"] for r in report["records"]]==[1,2,2,2,2]


def test_technical_success_can_still_disagree_with_human_label(store,config):
    repo = prepare_repository(store)
    seed(store,"获奖报道：我校斩获一等奖。",ident=9)
    n = repo.input(9)
    with repo.db:repo.add_label(ManualLabel(9,n.content_hash,n.input_hash,"human","synthetic-test-user",worth_recommending=True))
    payload = screening_payload(9)
    payload.update(is_participation_opportunity=False,category="news",missing_information=[],recommendation="exclude",review_required=False,
        evidence=[{"field":field,"quote":"斩获一等奖","value":None} for field in ["category","is_participation_opportunity"]])
    analyzer = analyzer_for(repo,config_for(config.base_url,config.key_env),lambda _:response(payload))
    report = execute_samples(repo,analyzer,[9])
    assert report["records"][0]["technical_success"]
    assert report["records"][0]["business_correct_against_human"] is False
    assert report["records"][0]["policy_review_signal"] and not report["stopped"]


def test_contact_and_coauthor_redaction_keeps_years_and_qualification():
    text = "我校全日制本科生可报名，2026年11月1日截止。\nQQ群：123456789，联系QQ 987654321。\n示例甲（23物联）、示例乙、示例丙（24计科）获奖。"
    clean = external_text(text)
    assert "我校全日制本科生可报名，2026年11月1日截止" in clean
    for private in ["123456789","987654321","示例甲","示例乙","示例丙"]:assert private not in clean
    assert "2026年" in external_text("竞赛（2026年）面向2024级本科生报名")


def test_failure_capture_is_opt_in_and_does_not_change_schema(store,config):
    repo = prepare_repository(store)
    fixed = config_for(config.base_url,config.key_env)
    analyzer = analyzer_for(repo,replace(fixed,capture_failure_diagnostics=False),lambda _:response({},content="bad JSON"))
    report = execute_samples(repo,analyzer,[34])
    assert "failure_evidence" not in report["records"][0]["diagnostic"]
    assert analyzer.contract.SCHEMA_VERSION=="llm-screening-v2"


def test_generated_redaction_marker_is_never_factual_evidence(store,config):
    repo = prepare_repository(store)
    seed(store,"QQ群：123456789。",ident=34)
    payload = screening_payload(34)
    payload["evidence"] = [{"field":"category","quote":"[联系或身份信息已移除]","value":None}]
    analyzer = analyzer_for(repo,config_for(config.base_url,config.key_env),lambda _:response(payload))
    report = execute_samples(repo,analyzer,ORDER)
    d = report["records"][0]["diagnostic"]
    assert report["stopped"] and report["api_requests"]==1
    assert d["evidence_ok"] is False and d["validation_error_path"]=="$.evidence[0].quote"


@pytest.mark.parametrize("violation",["output_tokens","reasoning"])
def test_protocol_budget_warnings_also_stop_after_one_post(store,config,violation):
    repo = prepare_repository(store)
    res = response(screening_payload(34),extra_message={"reasoning_content":"synthetic reasoning"} if violation=="reasoning" else {})
    body = json.loads(res.content)
    if violation=="output_tokens":body["usage"]={"prompt_tokens":10,"completion_tokens":4097,"total_tokens":4107}
    analyzer = analyzer_for(repo,config_for(config.base_url,config.key_env),lambda _:httpx.Response(200,json=body))
    report = execute_samples(repo,analyzer,ORDER)
    assert report["api_requests"]==1 and report["stopped"] and report["unprocessed_ids"]==[1,19,20,9]
    assert report["records"][0]["technical_success"]
