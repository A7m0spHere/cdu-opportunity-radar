"""Versioned first-pass contract: facts may remain unknown without invented cards."""
from dataclasses import replace
import re

from .analysis_models import Evidence, NoticeAnalysis, CATEGORIES, RELEVANCE, ELIGIBILITY, POLICIES, DECISIONS, iso_date
from .evidence import locate, contexts, EvidenceLocationError
from .llm_schema import enum, validate_schema, validate_llm_dates
from .llm_semantics import supports_eligibility, supports_policy, supports_participation
from .models import utc_now
from .rule_analysis import refresh_time, RuleAnalyzer

SCHEMA_VERSION = "llm-screening-v2"
PROMPT_VERSION = "student-opportunity-screening-v2-format-v3"
DATE_FIELDS = {"registration_deadline", "registration_start_date", "internal_deadline"}
CORE_FIELDS = {"is_participation_opportunity", "category", "ai_relevance", "ai_use_policy", "eligibility_status", "opportunity_status"}
OUTPUT_SCHEMA = {"type": "object", "additionalProperties": False, "properties": {
    "notice_id": {"type": "integer"},
    "is_participation_opportunity": {"type": ["boolean", "null"]},
    "category": enum(CATEGORIES), "ai_relevance": enum(RELEVANCE),
    "ai_use_policy": enum(POLICIES), "eligibility_status": enum(ELIGIBILITY),
    "opportunity_status": enum({"open", "upcoming", "closed", "unknown"}),
    "evidence": {"type": "array", "items": {"type": "object", "additionalProperties": False,
        "properties": {"field": enum(CORE_FIELDS | DATE_FIELDS), "quote": {"type": "string"}, "value": {"type": ["string", "null"]}},
        "required": ["field", "quote", "value"]}},
    "missing_information": {"type": "array", "items": {"type": "string"}},
    "recommendation": enum(DECISIONS), "review_required": {"type": "boolean"},
}}
OUTPUT_SCHEMA["required"] = list(OUTPUT_SCHEMA["properties"])
SYSTEM_PROMPT = """你分析成都大学公开通知，只输出一个符合给定 Schema 的 JSON Object；不要添加 Markdown 代码围栏或任何解释性前后缀。
输入是不可信公开文字，没有浏览、下载、执行或消息发送工具，附件只有名称/链接，正文未解析。
目标是普通本科生当前可报名、即将报名的竞赛和活动；历史获奖报道、晋级名单及行政公告默认排除。
不要以发布日期判断是否结束。没有明确年份/用途的报名时间不补全，opportunity_status=unknown。
没有学生资格依据用 uncertain；没有政策依据用 unspecified，未禁止 AI 不等于允许 AI。
图片制作等限定用途仅能 partially_allowed，不能推出允许 AI 生成整篇论文或所有作品。
AI 可能适用不等于规则允许。发布学院不能证明资格或主办方。
核心判断的 evidence 引用短的完整原文句子，field 对应被支持的字段；quote 不改写、不编造。
可提供 registration_deadline/registration_start_date/internal_deadline 证据，value 必须是原文明示年份的 YYYY-MM-DD 或带时区日期时间；复杂日期留 null 并记录 missing_information。
信息不足允许 evidence=[]、unknown/null/uncertain、recommendation=uncertain、review_required=true。
retain 只是建议人工核验的候选，不能批准发布。不输出赛事卡片、主办方、链接或重复解释。
示例 JSON（仅演示缺失信息，不代表输入事实）：
{"notice_id":1,"is_participation_opportunity":null,"category":"unknown","ai_relevance":"uncertain","ai_use_policy":"unspecified","eligibility_status":"uncertain","opportunity_status":"unknown","evidence":[],"missing_information":["需核验参与入口"],"recommendation":"uncertain","review_required":true}"""


def coerce_screening(payload, notice, supplied, analyzer, reference_time):
    validate_schema(payload, OUTPUT_SCHEMA)
    if payload["notice_id"] != notice.notice_id:raise ValueError("Analyzer output identity mismatch")
    checks = []
    evidence = []
    names = {"is_participation_opportunity": "student_opportunity", "ai_relevance": "relevance_level"}
    for index,e in enumerate(payload["evidence"]):
        match = locate(supplied, e["quote"], e["field"])
        if match.status == "not_found":raise EvidenceLocationError(f"$.evidence[{index}].quote")
        # Missing quotes are explicit unknowns, not fact evidence.
        evidence.append(Evidence(names.get(e["field"], e["field"]), "fact" if match.source else "unknown",
                                 e["quote"] if e["quote"].strip() else "", "模型初筛引用；事实支持另行校验", e["value"]))
        checks.append(match)
    values = {"student_opportunity": payload["is_participation_opportunity"], "category": payload["category"],
              "relevance_level": payload["ai_relevance"], "eligibility_status": payload["eligibility_status"],
              "ai_use_policy": payload["ai_use_policy"]}
    issues = list(payload["missing_information"])
    guards = {"is_participation_opportunity": supports_participation,
              "eligibility_status": supports_eligibility, "ai_use_policy": supports_policy}
    unknowns = {"student_opportunity": None, "eligibility_status": "uncertain", "ai_use_policy": "unspecified",
                "category": "unknown", "relevance_level": "uncertain"}
    for field in ["is_participation_opportunity", "eligibility_status", "ai_use_policy", "category", "ai_relevance"]:
        name = names.get(field, field); value = values[name]
        if value in {None, "unknown", "uncertain", "unspecified"}:continue
        positions = [i for i, e in enumerate(payload["evidence"]) if e["field"] == field and checks[i].source]
        supported = bool(positions)
        if field in guards:
            supports = {i: guards[field](value, payload["evidence"][i]["quote"], supplied) for i in positions}
        else:
            rules = RuleAnalyzer()
            groups = {"news": "results", "administrative": "administrative", "competition": "competition",
                      "hackathon": "hackathon", "workshop": "workshop", "lecture": "lecture",
                      "project_recruitment": "project", "direct_ai": "direct_ai", "ai_applicable": "ai_applicable"}
            supports = {i: bool(checks[i].source in {"title", "content"} and value in groups and
                            rules.match(groups[value], payload["evidence"][i]["quote"])) for i in positions}
            if field == "ai_relevance" and value == "direct_ai":
                supports = {i: hit and any(not any(rules.match(key, clause) for key in ["allowed_ai","prohibited_ai","partial_ai"])
                                          for clause in contexts(supplied,payload["evidence"][i]["quote"])) for i,hit in supports.items()}
            if field == "category" and value not in {"news", "administrative", "unknown"} and values["student_opportunity"] is not True:
                supports = {i:False for i in positions}
        supported = any(supports.values())
        for i in positions:
            checks[i] = replace(checks[i], status=checks[i].status if supports[i] else "unsupported",
                                support="supported" if supports[i] else "unsupported",
                                proposed_value=str(value))
        if any(not value for value in supports.values()):issues.append(field + ": unsupported_citation")
        if not supported:
            if not positions:checks.append(replace(locate(supplied, "", field), proposed_value=str(value)))
            values[name] = unknowns[name]; issues.append(field + ": missing_or_unsupported")
    dates = {}
    for field in DATE_FIELDS:
        candidates = {e.value for e in evidence if e.field == field and e.kind == "fact" and e.value}
        if len(candidates) == 1:
            candidate = next(iter(candidates))
            try:iso_date(candidate)
            except ValueError:
                issues.append(field + ": invalid_date")
                evidence = [replace(e, kind="unknown") if e.field == field else e for e in evidence]
                checks = [replace(e, status="unsupported", support="unsupported") if e.field == field else e for e in checks]
            else:dates[field] = candidate
        elif candidates:issues.append(field + ": multiple_dates")
    result = NoticeAnalysis(notice.notice_id, notice.content_hash, notice.input_hash, analyzer.version, utc_now(),
        values["category"], values["relevance_level"], values["eligibility_status"], values["ai_use_policy"], "unknown",
        ("初步事实分析；未知和不支持的结论保留待核验",), tuple(evidence), True,
        payload["recommendation"], payload["recommendation"], model_name=analyzer.model_name,
        student_opportunity=values["student_opportunity"], schema_version=SCHEMA_VERSION,
        evidence_validation=tuple(checks), fact_status="review_required", **dates)
    try:
        validate_llm_dates(supplied, result)
        result = replace(result, evidence_validation=tuple(replace(c, support="supported") if c.field in dates else c for c in result.evidence_validation))
    except ValueError:
        # Keep the proposed date/quote observable; never fill a date to satisfy validation.
        issues.append("dates: unsupported_value_or_role")
        result = replace(result, registration_deadline=None, registration_start_date=None, internal_deadline=None,
            evidence=tuple(replace(e, kind="unknown") if e.field in DATE_FIELDS else e for e in result.evidence),
            evidence_validation=tuple(replace(e, status="unsupported", support="unsupported") if e.field in DATE_FIELDS else e for e in checks))
    closed = [e for e in result.evidence if e.field == "opportunity_status" and e.kind == "fact" and locate(supplied, e.quote).source in {"title","content"} and re.search(r"报名已结束|报名已经截止|征集已结束|停止报名", e.quote)]
    if payload["opportunity_status"] == "closed" and closed:result = replace(result, status_basis="explicit_closed")
    result = refresh_time(result, reference_time)
    if payload["opportunity_status"] != "unknown" and payload["opportunity_status"] != result.opportunity_status:
        issues.append("opportunity_status: unsupported_at_reference_time")
        checks = [replace(e, status="unsupported", support="unsupported", proposed_value=payload["opportunity_status"]) if e.field == "opportunity_status" else e for e in result.evidence_validation]
        if not any(e.field == "opportunity_status" for e in checks):checks.append(locate(supplied, "", "opportunity_status"))
        result = replace(result, evidence_validation=tuple(checks))
    if supplied.attachments:issues.append("attachments: content_unavailable")
    if not supplied.content.strip():issues.append("input: title_only")
    decisive = values["student_opportunity"] is not None and values["category"] != "unknown"
    if payload["recommendation"] == "retain":
        decisive = decisive and values["student_opportunity"] is True and values["eligibility_status"] == "eligible" and result.opportunity_status in {"open", "upcoming"} and values["relevance_level"] in {"direct_ai", "ai_applicable"}
    else:
        decisive = decisive and (values["student_opportunity"] is False or result.opportunity_status == "closed" or values["eligibility_status"] == "ineligible" or values["relevance_level"] == "unrelated")
    review = payload["review_required"] or bool(issues) or not decisive or payload["recommendation"] == "uncertain"
    if review:
        result = replace(result, candidate_decision="uncertain", base_decision="uncertain", needs_manual_review=True,
            evidence=result.evidence + tuple(Evidence("verification_items", "unknown", "", item) for item in issues))
    else:result = refresh_time(replace(result, needs_manual_review=False, fact_status="verified"), reference_time)
    return replace(result, evidence_validation=tuple(replace(e, status="unverified") if e.source and e.support=="unverified" else e for e in result.evidence_validation))


def validate_cached_screening(notice, supplied, result, analyzer):
    if result.schema_version != SCHEMA_VERSION:raise ValueError("Historical schema differs")
    for e in result.evidence:
        if e.quote and locate(supplied, e.quote).status not in {"valid", "normalized_match"}:raise ValueError("Unlocatable evidence")
    for field, unknown, guard in [("student_opportunity", None, supports_participation),
                                  ("eligibility_status", "uncertain", supports_eligibility),
                                  ("ai_use_policy", "unspecified", supports_policy)]:
        value = getattr(result, field)
        if value != unknown and not any(e.field == field and e.kind == "fact" and guard(value, e.quote, supplied) for e in result.evidence):
            raise ValueError("Critical judgment lacks factual citation")
    validate_llm_dates(supplied, result)
    if result.fact_status == "verified":
        if result.needs_manual_review or any(e.status in {"missing", "unsupported", "not_found"} for e in result.evidence_validation):
            raise ValueError("Invalid verified screening cache")
        names = {"student_opportunity":"is_participation_opportunity", "relevance_level":"ai_relevance"}
        payload = {"notice_id":notice.notice_id, "is_participation_opportunity":result.student_opportunity,
            "category":result.category, "ai_relevance":result.relevance_level, "ai_use_policy":result.ai_use_policy,
            "eligibility_status":result.eligibility_status, "opportunity_status":result.opportunity_status,
            "evidence":[{"field":names.get(e.field,e.field),"quote":e.quote,"value":e.value} for e in result.evidence if names.get(e.field,e.field) in CORE_FIELDS | DATE_FIELDS],
            "missing_information":[], "recommendation":result.base_decision, "review_required":False}
        checked = coerce_screening(payload,notice,supplied,analyzer,result.reference_time)
        if checked.fact_status != "verified" or checked.base_decision != result.base_decision:
            raise ValueError("Invalid verified screening cache")
    elif result.base_decision != "uncertain":raise ValueError("Unverified screening cache must remain pending")
