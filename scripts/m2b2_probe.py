"""Fixed five-notice probe. Default prepares only; --send requires a frozen plan."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import sqlite3

from cdu_radar.analysis_models import fingerprint, reference_datetime
from cdu_radar.analysis_store import AnalysisRepository
from cdu_radar.labels import atomic_text
from cdu_radar.llm import LLMConfig, LLMAnalyzer
from cdu_radar.llm_workflow import LLMLedger, readonly_repository, run_batch
from cdu_radar.models import utc_now
from cdu_radar.storage import Store

ORDER = [34,1,19,20,9]
HISTORY = "llm-v1:89e8a0e080bd523db08b159c"
REFERENCE = "2026-10-14T00:00:00+08:00"


def config_for(base_url, key_env="CDU_LLM_API_KEY"):
    return LLMConfig(base_url,"deepseek-v4.1-flash",key_env=key_env, schema_mode="screening_v2",
        protocol_profile="deepseek",thinking_mode="disabled",temperature=0,response_format="json_object",
        output_token_parameter="max_tokens",max_output_tokens=4096,max_input_chars=20000,
        timeout=90,retries=0,batch_limit=1,stop_after_format_failures=1,capture_failure_diagnostics=True)


def parameters(config):
    return {"model":config.model,"response_format":{"type":config.response_format},
            "thinking":{"type":config.thinking_mode},"temperature":config.temperature,
            "max_tokens":config.max_output_tokens,"stream":False}


def prepare_plan(repository, config, source_path, ids=ORDER):
    if len(ids)>5 or len(set(ids))!=len(ids):raise ValueError("Invalid probe budget/ids")
    analyzer = LLMAnalyzer(config)
    samples = []
    for ident in ids:
        notice = repository.input(ident)
        _,messages = analyzer.prepare(notice)
        label = repository.latest_label(notice)
        old = repository.cached(notice,HISTORY)
        samples.append({"notice_id":ident,"title":notice.title,"input_hash":notice.input_hash,
            "human_worth_recommending":label.worth_recommending if label else None,
            "human_reference_time":label.reference_time if label else None,
            "human_dimensions_available":bool(label and any(getattr(label,k) is not None for k in ["eligibility_status","ai_use_policy","registration_deadline","opportunity_status"])),
            "historical_state":old.analysis_state if old else "not_analyzed",
            "historical_reasons":list(old.analysis_reason) if old else [],
            "input_chars":sum(len(m["content"]) for m in messages),
            "request_payload":dict(parameters(config),messages=messages)})
    material = {"source_sha256":hashlib.sha256(source_path.read_bytes()).hexdigest(),
        "harness_sha256":hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "diagnostic_code_sha256":hashlib.sha256((Path(__file__).resolve().parents[1]/"cdu_radar/llm_debug.py").read_bytes()).hexdigest(),
        "analyzer_version":analyzer.version,"prompt_version":analyzer.prompt_version,"schema_version":analyzer.schema_version,
        "config":asdict(config),"request_order":list(ids),"reference_time":REFERENCE,
        "payload_hashes":[fingerprint(s["request_payload"]) for s in samples],
        "input_hashes":[s["input_hash"] for s in samples],"max_post_requests":5}
    return {"created_at":utc_now(),"dry_run":True,"api_requests":0,"approval_required":True,
            "plan_hash":fingerprint(material),"frozen":material,"samples":samples,
            "key_available":bool(os.environ.get(config.key_env)),
            "cost_estimate":None,"cost_note":"Third-party prices/tokenizer unknown; output cap 20480 tokens if honored"}


def execute_samples(repository, analyzer, ids, *, report_sink=lambda _:None):
    if len(ids)>5 or analyzer.config.retries!=0 or analyzer.config.batch_limit!=1 or analyzer.config.max_output_tokens>4096:
        raise ValueError("Probe safety limits differ")
    output = {"api_requests":0,"records":[],"stopped":False,"stop_reason":None,"unprocessed_ids":list(ids)}
    for index,ident in enumerate(ids):
        notice = repository.input(ident)
        if repository.db.execute("SELECT 1 FROM llm_requests WHERE notice_id=? AND input_hash=? AND analyzer_version=? LIMIT 1",(ident,notice.input_hash,analyzer.version)).fetchone():
            raise ValueError("Probe already attempted; no automatic replay or paid retry")
        before = repository.db.execute("SELECT count(*) FROM llm_requests WHERE analyzer_version=?",(analyzer.version,)).fetchone()[0]
        batch = run_batch(repository,analyzer,[ident],reference_time=REFERENCE)
        after = repository.db.execute("SELECT count(*) FROM llm_requests WHERE analyzer_version=?",(analyzer.version,)).fetchone()[0]
        row = repository.db.execute("SELECT r.*,d.payload diagnostic FROM llm_requests r LEFT JOIN llm_request_diagnostics d USING(request_uid) WHERE notice_id=? AND analyzer_version=? ORDER BY started_at DESC,attempt DESC LIMIT 1",(ident,analyzer.version)).fetchone()
        diagnostic = json.loads(row["diagnostic"]) if row and row["diagnostic"] else {}
        result = batch["analyses"][0]
        notice = repository.input(ident); label = repository.latest_label(notice)
        old = repository.cached(notice,HISTORY)
        decision = result["candidate_decision"]
        technical_ok = result["analysis_state"]=="success" and all(diagnostic.get(k) is True for k in ["request_ok","json_ok","schema_ok","evidence_ok"])
        unknown = decision=="uncertain" or result["fact_status"]=="review_required"
        correct = (decision=="retain")==label.worth_recommending if technical_ok and not unknown and label and label.worth_recommending is not None else None
        old_correct = bool(old and old.analysis_state=="success" and old.candidate_decision in {"retain","exclude"} and
                           label and (old.candidate_decision=="retain")==label.worth_recommending)
        output["records"].append({"notice_id":ident,"gate":1 if index==0 else 2,"cached":bool(batch["cache_hits"]),
            "technical_success":technical_ok,"business_correct_against_human":correct,
            "historical_state":old.analysis_state if old else "not_analyzed",
            "historical_decision":old.candidate_decision if old and old.analysis_state=="success" else None,
            "recovered_historical_technical_failure":bool(old and old.analysis_state=="failed" and technical_ok),
            "regressed_historically_correct_decision":correct is False if old_correct and correct is not None else None,
            "abstained_after_historically_correct_decision":old_correct and technical_ok and unknown,
            "policy_review_signal":ident==9,
            "business_unknown":unknown,"analysis":result,"diagnostic":diagnostic,
            "usage":{k:row[k] for k in ["prompt_tokens","completion_tokens","total_tokens"]} if row else None})
        output["api_requests"] += after-before
        if after-before>1 or output["api_requests"]>5:raise AssertionError("Provider violated frozen request budget")
        output["unprocessed_ids"] = ids[index+1:]
        if not technical_ok:
            output.update(stopped=True,stop_reason="technical_failure")
        elif row and row["completion_tokens"] is not None and row["completion_tokens"]>analyzer.config.max_output_tokens:
            output.update(stopped=True,stop_reason="output_token_cap_exceeded")
        elif diagnostic.get("reasoning_chars",0) and analyzer.config.thinking_mode=="disabled":
            output.update(stopped=True,stop_reason="unexpected_reasoning_requires_protocol_review")
        report_sink(output)
        if output["stopped"]:break
    return output


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db",type=Path,default=Path("data/m2b.sqlite3"))
    parser.add_argument("--plan",type=Path,default=Path("data/m2b2-plan.json"))
    parser.add_argument("--base-url",help="Existing user provider; no built-in service")
    parser.add_argument("--key-env",default="CDU_LLM_API_KEY")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--send",action="store_true")
    mode.add_argument("--dry-run",action="store_true")
    parser.add_argument("--approved-plan-hash")
    parser.add_argument("--run-db",type=Path,default=Path("data/m2b2.sqlite3"))
    parser.add_argument("--output",type=Path,default=Path("data/m2b2-results.json"))
    args = parser.parse_args(argv)
    frozen = json.loads(args.plan.read_text("utf-8")) if args.send else None
    base_url = args.base_url or (frozen["frozen"]["config"]["base_url"] if frozen else os.environ.get("CDU_LLM_BASE_URL"))
    if not base_url:raise ValueError("Specify the existing provider base URL; no request sent")
    config = config_for(base_url,args.key_env)
    with readonly_repository(args.db) as repo:plan = prepare_plan(repo,config,args.db)
    if not args.send:
        if args.plan.exists():raise ValueError("Plan exists; select a new path")
        if args.plan.suffix.lower()!=".json" or args.plan.resolve()==args.db.resolve():raise ValueError("Plan must be separate JSON")
        atomic_text(args.plan,json.dumps(plan,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"plan":str(args.plan),"plan_hash":plan["plan_hash"],"api_requests":0,"key_available":plan["key_available"]}))
        return 0
    if not args.approved_plan_hash or frozen["plan_hash"]!=args.approved_plan_hash or plan["plan_hash"]!=frozen["plan_hash"]:
        raise ValueError("Approved plan differs; prepare/review again")
    if not os.environ.get(config.key_env," ").strip():raise ValueError("Configure the existing provider key locally")
    if args.run_db.exists() or args.output.exists():raise ValueError("Run artifacts exist; no automatic paid resume")
    if args.run_db.resolve()==args.db.resolve() or args.output.suffix.lower()!=".json" or args.output.resolve() in {args.plan.resolve(),args.db.resolve(),args.run_db.resolve()}:
        raise ValueError("Run artifacts must be independent")
    args.run_db.parent.mkdir(parents=True,exist_ok=True)
    with readonly_repository(args.db) as repo:
        with sqlite3.connect(args.run_db) as target:repo.db.backup(target)
    if hashlib.sha256(args.db.read_bytes()).hexdigest()!=frozen["frozen"]["source_sha256"]:raise ValueError("Source changed during preparation; no requests sent")
    with Store(args.run_db) as store:
        repo = AnalysisRepository(store); ledger = LLMLedger(repo)
        if [repo.input(i).input_hash for i in ORDER]!=frozen["frozen"]["input_hashes"]:raise ValueError("Copied inputs differ; no requests sent")
        analyzer = LLMAnalyzer(config,event_sink=ledger.record)
        def save(report):
            atomic_text(args.output,json.dumps(dict(report,plan_hash=frozen["plan_hash"],analyzer_version=analyzer.version),ensure_ascii=False,indent=2),encoding="utf-8")
        report = execute_samples(repo,analyzer,ORDER,report_sink=save)
    print(json.dumps({"output":str(args.output),"api_requests":report["api_requests"],"stopped":report["stopped"],"stop_reason":report["stop_reason"]}))
    return 1 if report["stopped"] else 0


if __name__=="__main__":raise SystemExit(main())
