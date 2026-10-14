"""Read-only historical diagnosis. Missing observations are never reconstructed."""
from collections import Counter
import hashlib
import json
import re

from .analysis_models import reference_datetime
from .evaluation import evaluation_layers
from .llm import validation_stage
from .rule_analysis import RuleAnalyzer


def historical_results(repository, version, notice_ids=None):
    if not re.fullmatch(r"llm-v[12]:[a-f0-9]{24}", version):raise ValueError("Invalid historical analysis version")
    ids = repository.ids() if notice_ids is None else notice_ids
    if len(set(ids)) != len(ids) or not set(ids) <= set(repository.ids()):raise ValueError("Invalid history notice ids")
    metadata = [dict(r) for r in repository.db.execute(
        "SELECT DISTINCT model,prompt_version,schema_version FROM llm_requests WHERE analyzer_version=?", (version,))]
    if len(metadata) != 1:raise ValueError("Historical version missing or mixes schemas")
    results = [repository.cached(repository.input(i), version) for i in ids]
    return {"archive_only":True, "revalidated":False, "api_requests":0, "analyzer_version":version,
            **metadata[0], "analyses":[r.to_dict() for r in results if r is not None],
            "missing_ids":[i for i,r in zip(ids,results) if r is None]}


def request_stages(repository, version, notice_ids=None):
    exists = repository.db.execute("SELECT 1 FROM sqlite_master WHERE name='llm_requests'").fetchone()
    rows = [] if not exists else [dict(r) for r in repository.db.execute("SELECT * FROM llm_requests WHERE analyzer_version=?",(version,))]
    if notice_ids is not None:rows = [r for r in rows if r["notice_id"] in notice_ids]
    diagnostic_exists = repository.db.execute("SELECT 1 FROM sqlite_master WHERE name='llm_request_diagnostics'").fetchone()
    stages = {key:[] for key in ["request_ok","json_ok","direct_json_ok","schema_ok","evidence_ok","analysis_ok"]}
    parsing = Counter()
    observable = 0
    for row in rows:
        saved = repository.db.execute("SELECT payload FROM llm_request_diagnostics WHERE request_uid=?",(row["request_uid"],)).fetchone() if diagnostic_exists else None
        diagnostic = json.loads(saved[0]) if saved else None
        if diagnostic is not None:observable += 1
        else:
            category = row["error_category"]
            diagnostic = {"request_ok":row["status_code"]==200 if row["status_code"] is not None else None if category=="started" else False,
                          "analysis_ok":True if category is None else None if category=="started" else False,
                          "json_ok":True if category in {None,"schema_validation"} else False if category=="invalid_json" else None,
                          "schema_ok":True if category is None else None, "evidence_ok":True if category is None else None}
        for key in stages:stages[key].append(diagnostic.get(key))
        method = diagnostic.get("parse_method")
        parsing[method if method in {"direct_json","normalized_markdown_fence","invalid_json","unsupported_wrapper"} else "unobservable_or_not_reached"] += 1
    metrics = {}
    for key, values in stages.items():
        passed = sum(v is True for v in values); failed = sum(v is False for v in values); unknown = len(values)-passed-failed
        metrics[key.removesuffix("_ok") + "_success"] = {
            "passed":passed, "failed":failed, "unknown_or_not_reached":unknown, "denominator_requests":len(values),
            "rate":passed/len(values) if values and not unknown else None,
            "rate_lower_bound":passed/len(values) if values else None,
            "note":"Sequential stages; unobserved/not reached are not measured failures"}
    return {"scope":"requests within one analyzer version, including retries", "requests":len(rows),
            "diagnostic_requests":observable, "legacy_unobservable_requests":len(rows)-observable, "stages":metrics,
            "json_parsing":{method:parsing[method] for method in ["direct_json","normalized_markdown_fence","invalid_json","unsupported_wrapper","unobservable_or_not_reached"]},
            "json_note":"json_success includes strict fence normalization; direct_json_success measures native JSON Objects separately"}


def diagnose(repository, comparison_path, errors_path, *, reference_time=None):
    comparison = json.loads(comparison_path.read_text(encoding="utf-8"))
    errors = json.loads(errors_path.read_text(encoding="utf-8"))
    if not isinstance(errors.get("llm"), list):raise ValueError("Invalid historical errors report")
    if errors["llm"] != comparison["llm"]["error_samples"]:raise ValueError("Historical error/comparison reports differ")
    version = comparison["analyzer_version"]
    history = historical_results(repository, version)
    old_predictions = {p["notice_id"]:p for p in comparison["llm"]["predictions"]}
    saved = {p["notice_id"]:p for p in history["analyses"]}
    if set(old_predictions) != set(saved):raise ValueError("Historical report/cache coverage differs")
    for i, p in old_predictions.items():
        if p["state"] != saved[i]["analysis_state"] or p["decision"] != saved[i]["candidate_decision"]:
            raise ValueError("Historical report/cache state differs")
    failures = []; primary = Counter(); details = Counter(); flags = Counter(); review = []
    notice_stage_counts = {name:{"passed":0,"failed":0,"unknown_or_not_reached":0} for name in ["json","schema","evidence_location","complete_analysis"]}
    for i, result in saved.items():
        notice = repository.input(i); truth = repository.latest_label(notice)
        if truth and truth.worth_recommending != old_predictions[i]["truth"]:raise ValueError("Historical labels/report differ")
        input_flags = []
        if not notice.content.strip():input_flags.append("title_only")
        if notice.attachments:input_flags.append("unparsed_attachments")
        if len(re.findall(r"20\d{2}[年./-]\d{1,2}[月./-]\d{1,2}", notice.content)) > 1:input_flags.append("multiple_date_mentions")
        if re.search(r"仅限|推荐|本科|研究生|年级|专业", notice.content):input_flags.append("eligibility_context_needs_review")
        if result["analysis_state"] == "failed":
            match = re.search(r"分析失败：([A-Za-z_]+)(?:/([A-Za-z_]+))?", " ".join(result["analysis_reason"]))
            category, detail = match.groups() if match else ("unknown_unobservable", None)
            stage = validation_stage(detail) if category == "schema_validation" else "json" if category == "invalid_json" else "transport" if category in {"connection","timeout","authentication","forbidden","rate_limited","server_error","http_error"} else "unknown_unobservable"
            group = {"transport":"A_network_http", "json":"B_output_format", "schema":"B_output_format", "evidence_location":"C_evidence_location", "fact_support":"D_fact_support"}.get(stage,"unknown_unobservable")
            primary[group] += 1; details[detail or category] += 1; flags.update(input_flags)
            notice_stage_counts["json"]["failed" if stage=="json" else "passed" if category=="schema_validation" else "unknown_or_not_reached"] += 1
            notice_stage_counts["schema"]["failed" if stage=="schema" else "passed" if stage in {"evidence_location","fact_support"} else "unknown_or_not_reached"] += 1
            notice_stage_counts["evidence_location"]["failed" if stage=="evidence_location" else "passed" if stage=="fact_support" else "unknown_or_not_reached"] += 1
            notice_stage_counts["complete_analysis"]["failed"] += 1
            failures.append({"notice_id":i,"primary_stage":stage,"primary_group":group,"observed_code":category + ("/"+detail if detail else ""),
                "request_error_history":[dict(row) for row in repository.db.execute(
                    "SELECT analyzer_version,error_category,count(*) attempts FROM llm_requests WHERE notice_id=? AND input_hash=? GROUP BY analyzer_version,error_category ORDER BY analyzer_version,error_category",
                    (i,notice.input_hash))],
                "secondary_input_flags":input_flags, "input_flags_are_causal":False,
                "root_cause":"unknown_unobservable", "observation":"Saved first rejection only; no failed final content, quote or finish_reason",
                "human_positive":truth.worth_recommending if truth else None})
        else:
            for counts in notice_stage_counts.values():counts["passed"] += 1
        if truth and truth.worth_recommending is True:
            point = reference_datetime(reference_time or truth.reference_time or truth.reference_date)
            rule = RuleAnalyzer().analyze(notice, reference_time=point)
            reasons = []
            if rule.category in {"news","administrative"}:reasons.append("historical_news_or_administrative_signal")
            if rule.opportunity_status=="closed":reasons.append("explicit_registration_deadline_passed")
            if rule.eligibility_status=="ineligible" or "研究生" in notice.title:reasons.append("undergraduate_eligibility_needs_review")
            if reasons:review.append({"notice_id":i,"human_worth_recommending":True,"signals":reasons,"action":"manual_recheck_only; label_unchanged"})
    predictions = [dict(p, fact_status=saved[p["notice_id"]].get("fact_status","not_checked")) for p in comparison["llm"]["predictions"]]
    n = len(failures)
    return {"report_version":"m2b1-offline-diagnosis-v1","api_requests_sent":0,"analyzer_version":version,
        "model":history["model"], "prompt_version":history["prompt_version"],"schema_version":history["schema_version"],
        "failed_notices":n, "successful_notices":len(saved)-n,
        "primary_counts":{key:{"count":primary[key],"ratio":primary[key]/n if n else None} for key in ["A_network_http","B_output_format","C_evidence_location","D_fact_support","E_insufficient_information","unknown_unobservable"]},
        "observed_detail_counts":dict(sorted(details.items())), "secondary_input_flags":dict(flags),
        "all_failed_root_causes_unobservable":n,"failures":sorted(failures,key=lambda r:r["notice_id"]),
        "technical_requests":request_stages(repository,version), "technical_notices":notice_stage_counts,
        "evaluation":evaluation_layers(predictions),
        "legacy_retention_metrics":{k:comparison["llm"][k] for k in ["precision","recall","f1","confusion_matrix"]},
        "human_label_policy_recheck":review,
        "source_hashes":{path.name:hashlib.sha256(path.read_bytes()).hexdigest() for path in [comparison_path,errors_path]},
        "historical_error_report":{"rows":len(errors["llm"]), "types":dict(Counter(kind for row in errors["llm"] for kind in row["error_types"])),
            "kind":"business disagreement list, not raw output or technical validation trace"},
        "limits":["Input flags are risk signals, not reconstructed model errors", "First failure prevents observing later stages",
                  "Cannot distinguish whitespace, HTML cleanup, paraphrase and fabrication without the failed quote",
                  "Historical reports/cache are archived under their original schema; no v1/v2 metric pooling"]}
