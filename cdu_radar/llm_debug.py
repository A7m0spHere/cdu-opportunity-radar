"""Opt-in, lossy failure evidence. Never persist arbitrary model strings."""
import hashlib
import json

from .analysis_models import CATEGORIES, RELEVANCE, ELIGIBILITY, POLICIES, DECISIONS, STATUSES

SAFE_KEYS = {"notice_id", "is_participation_opportunity", "category", "ai_relevance", "relevance_level",
    "ai_use_policy", "eligibility_status", "opportunity_status", "evidence", "missing_information",
    "recommendation", "review_required", "field", "quote", "value", "kind", "reason", "student_opportunity",
    "candidate_decision", "status_basis", "needs_manual_review", "analysis_reason", "verification_items",
    "event_name", "organizer", "target_audience", "requirements_summary", "registration_url",
    "registration_deadline", "registration_start_date", "internal_deadline", "event_start_date", "event_end_date",
    "internal_event_date", "attachments", "input_integrity"}
SAFE_VALUES = CATEGORIES | RELEVANCE | ELIGIBILITY | POLICIES | DECISIONS | STATUSES | SAFE_KEYS | {"fact", "inference", "unknown", "deadline", "explicit_closed", "registration_start"}


def text_summary(value):
    return {"text_redacted":True, "chars":len(value), "sha256":hashlib.sha256(value.encode("utf-8",errors="surrogatepass")).hexdigest()}


def projection(value, depth=0, path="$", lengths=None, expected_notice_id=None):
    if lengths is None:lengths = {}
    if depth > 12:return "[depth limit]"
    if isinstance(value, str):
        if value in SAFE_VALUES:return value
        lengths[path] = text_summary(value)
        return "[text redacted]"
    if value is None or type(value) is bool:return value
    if type(value) in {int,float}:
        return value if path=="$.notice_id" and type(value) is int and value==expected_notice_id else 0
    if isinstance(value, list):
        # Contract permits 100 entries. Keep indices for field-path reproduction.
        return [projection(item,depth+1,path+f"[{i}]",lengths,expected_notice_id) for i,item in enumerate(value[:100])]
    if isinstance(value, dict):
        result = {}
        for index,(key,item) in enumerate(list(value.items())[:100]):
            safe_key = key if key in SAFE_KEYS else f"unknown_key_{index}"
            result[safe_key] = projection(item,depth+1,path+"."+safe_key,lengths,expected_notice_id)
        return result
    return "[unsupported type]"


def failure_evidence(content, data, diagnostic, expected_notice_id=None, api_excerpt=None):
    """Bounded structural excerpts, not a substituted response or semantic replay."""
    record = {"format":"redacted-failure-v1", "lossy":True,
              "note":"Only structure/types survive. Free text and numbers are redacted; not the original model response.",
              "field_path":diagnostic.get("validation_error_path") or diagnostic.get("schema_error_path") or "$"}
    if content is None and api_excerpt is not None:
        content = api_excerpt
        record["excerpt_source"] = "api_envelope"
    else:record["excerpt_source"] = "final_content"
    if isinstance(content, str):
        record.update(content_sha256=hashlib.sha256(content.encode("utf-8",errors="surrogatepass")).hexdigest(), content_chars=len(content))
        # All words/digits are masked, including contact details and unexpected credentials.
        # Preserve punctuation, controls and backslashes to show malformed JSON structure.
        center = 0
        error_line = diagnostic.get("json_error_line") or diagnostic.get("api_json_error_line")
        error_column = diagnostic.get("json_error_column") or diagnostic.get("api_json_error_column")
        if error_line:
            lines = content.splitlines(keepends=True)
            center = sum(len(line) for line in lines[:error_line-1]) + (error_column or 1)-1
        start = max(0,center-192); window = content[start:start+384]
        record.update(excerpt_start=start, structural_excerpt="".join("x" if c.isalpha() else "0" if c.isdigit() else c if c in '{}[],:"\\-+. /\r\n\t' else "?" for c in window))
    if data is not None:
        lengths = {}
        value = projection(data,lengths=lengths,expected_notice_id=expected_notice_id)
        encoded = json.dumps(value,ensure_ascii=True)
        record["redacted_payload_excerpt"] = encoded[:4096]
        record["projection_truncated"] = len(encoded)>4096
        record["text_metadata"] = dict(list(lengths.items())[:100])
    return record
