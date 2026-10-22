"""Bounded OpenAI-compatible transport and Analyzer; no tools or secret logging."""
from dataclasses import asdict, dataclass, replace
from importlib.resources import files
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import time
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
import uuid

import httpx

from .analysis_models import InputLink, fingerprint
from .llm_schema import (OUTPUT_SCHEMA, PROMPT_VERSION, SCHEMA_VERSION,
                         SYSTEM_PROMPT, TEXT_FIELDS, coerce_result)
from .llm_schema import validate_llm_dates
from .llm_schema import SchemaValidationError
from .models import utc_now
from .privacy import sanitize
from .evidence import locate, EvidenceLocationError
from .llm_json import strict_json, parse_model_json, ModelJSONError, PARSER_VERSION
from .tls import trusted_context


SCHEMA_DETAILS = {
    "Invalid LLM schema type or enum": "invalid_type_or_enum",
    "Missing or extra LLM fields": "missing_or_extra_fields",
    "Missing analysis reasons/evidence": "missing_reason_or_evidence",
    "Unknown evidence field": "unknown_evidence_field",
    "Unlocatable evidence": "unlocatable_evidence",
    "Critical judgment lacks factual citation": "missing_critical_citation",
    "AI policy quote lacks the claimed permission/prohibition": "unsupported_ai_policy",
    "Critical value lacks factual citation": "missing_value_citation",
    "Organizer not supplied": "unprovided_organizer",
    "Closed status lacks explicit evidence": "unprovided_closed_status",
    "Date evidence must preserve quoted value and purpose": "invalid_date_evidence",
    "Multiple dates cannot become a single deadline/start date": "collapsed_multiple_dates",
    "Date value lacks a matching role-labelled fact quote": "invalid_date_evidence",
    "Fact quote is not supplied input": "unlocatable_evidence",
    "Registration URL was not supplied": "unprovided_registration_url",
    "Deadline basis requires a deadline": "missing_deadline_basis",
    "Registration start basis requires a start": "missing_start_basis",
    "Eligibility quote lacks the claimed qualification": "unsupported_eligibility",
    "Participation quote contradicts the claimed opportunity": "unsupported_participation",
    "Analyzer output identity mismatch": "identity_mismatch",
    "LLM array exceeds bound": "invalid_type_or_enum",
    "LLM field exceeds bound": "invalid_type_or_enum",
    "Fact evidence requires a nonempty quote": "missing_critical_citation",
}

LOCATION_DETAILS = {"unlocatable_evidence"}
SHAPE_DETAILS = {"invalid_type_or_enum", "missing_or_extra_fields", "unknown_evidence_field", "identity_mismatch"}


def validation_stage(detail):
    if detail in LOCATION_DETAILS:return "evidence_location"
    if detail in SHAPE_DETAILS:return "schema"
    if detail == "invalid_schema":return "unknown_unobservable"
    return "fact_support"


class LLMError(ValueError):
    def __init__(self, category, retryable=False, detail=None):
        self.category, self.retryable, self.detail = category, retryable, detail
        super().__init__(category)  # Never retain API exception/response text.


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    key_env: str = "CDU_LLM_API_KEY"
    credential_file: str | None = None
    analysis_mode: str = "legacy"
    temperature: float | None = None
    thinking_mode: str | None = None
    timeout: float = 60
    retries: int = 1
    max_input_chars: int = 20000
    max_output_tokens: int = 4096
    batch_limit: int = 3
    stop_after_format_failures: int = 2
    response_format: str = "json_object"
    schema_mode: str = "detailed_v1"
    protocol_profile: str = "generic"
    output_token_parameter: str = "max_tokens"
    capture_failure_diagnostics: bool = False
    prompt_file: str | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cost_currency: str | None = None

    def __post_init__(self):
        if self.analysis_mode not in {'legacy', 'classification_review_v2', 'automatic_review_v3'}:
            raise ValueError('Invalid analysis mode')
        if self.credential_file is not None and (not isinstance(self.credential_file,str) or not Path(self.credential_file).is_absolute()):
            raise ValueError('Credential reference must be absolute')
        if type(self.capture_failure_diagnostics) is not bool:raise ValueError("Invalid failure capture mode")
        try:
            url = urlsplit(self.base_url)
            loopback = url.hostname == "localhost" or bool(url.hostname and ipaddress.ip_address(url.hostname).is_loopback)
        except ValueError:
            url = urlsplit(self.base_url)
            loopback = url.hostname == "localhost"
        if (not url.hostname or url.username or url.password or url.query or url.fragment
                or not (url.scheme == "https" or url.scheme == "http" and loopback)):
            raise ValueError("LLM base URL must be HTTPS (HTTP allowed only for loopback)")
        if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,128}", self.model) or self.model.startswith("sk-"):
            raise ValueError("Invalid configured model identifier")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.key_env): raise ValueError("Invalid key environment name")
        for name in ["timeout", "temperature", "input_price_per_million", "output_price_per_million"]:
            value = getattr(self, name)
            if value is not None and (type(value) not in {float, int} or not math.isfinite(value) or value < 0):
                raise ValueError("Invalid LLM numeric configuration")
        if self.timeout <= 0 or self.temperature is not None and self.temperature > 2:
            raise ValueError("Invalid timeout/temperature")
        for name in ["retries", "max_input_chars", "max_output_tokens", "batch_limit", "stop_after_format_failures"]:
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "retries" else 1): raise ValueError("Invalid LLM limit")
        if self.retries > 3 or self.batch_limit > 100: raise ValueError("Retry/batch safety bound exceeded")
        if self.stop_after_format_failures > 10:raise ValueError("Invalid format failure stop bound")
        if self.response_format not in {"json_schema", "json_object", "none"}: raise ValueError("Invalid response format mode")
        if self.thinking_mode not in {None, "enabled", "disabled"}: raise ValueError("Invalid optional thinking mode")
        if self.schema_mode not in {"detailed_v1", "screening_v2"}:raise ValueError("Invalid schema mode")
        if self.protocol_profile not in {"generic", "deepseek"}:raise ValueError("Invalid protocol profile")
        if self.output_token_parameter not in {"max_tokens", "max_completion_tokens"}:raise ValueError("Invalid output token parameter")
        priced = [self.input_price_per_million is not None, self.output_price_per_million is not None]
        if any(priced) and (not all(priced) or not self.cost_currency): raise ValueError("Both prices and currency are required")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        base, model = env.get("CDU_LLM_BASE_URL", ""), env.get("CDU_LLM_MODEL", "")
        if not base or not model: raise ValueError("Set CDU_LLM_BASE_URL and CDU_LLM_MODEL")
        values = {"base_url": base, "model": model}
        converters = {"temperature": float, "timeout": float, "retries": int, "max_input_chars": int,
                      "max_output_tokens": int, "batch_limit": int, "stop_after_format_failures":int, "input_price_per_million": float,
                      "output_price_per_million": float}
        for name in cls.__dataclass_fields__:
            if name in {"base_url", "model"}: continue
            raw = env.get("CDU_LLM_" + name.upper())
            if raw is None or raw == "": continue
            try:
                if name == "capture_failure_diagnostics":
                    if raw.lower() not in {"true","false","1","0"}:raise ValueError()
                    values[name] = raw.lower() in {"true","1"}
                else:values[name] = converters[name](raw) if name in converters else raw
            except (ValueError, TypeError): raise ValueError("Invalid CDU_LLM_" + name.upper()) from None
        return cls(**values)

    def identity(self):
        names = ["base_url", "model", "temperature", "thinking_mode", "max_input_chars", "max_output_tokens", "response_format", "schema_mode", "protocol_profile", "output_token_parameter"]
        return {name: getattr(self, name) for name in names}


def usage_values(body):
    usage = body.get("usage") if isinstance(body, dict) else None
    keys = ["prompt_tokens", "completion_tokens", "total_tokens"]
    if not isinstance(usage, dict) or any(type(usage.get(k)) is not int or usage[k] < 0 for k in keys): return None
    if usage["total_tokens"] != usage["prompt_tokens"] + usage["completion_tokens"]: return None
    result = {key: usage[key] for key in keys}
    details = usage.get("prompt_tokens_details", {})
    if isinstance(details, dict) and type(details.get("cached_tokens")) is int and 0 <= details["cached_tokens"] <= usage["prompt_tokens"]:
        result["cached_tokens"] = details["cached_tokens"]
    return result


class Provider(Protocol):
    def complete(self, messages, validator, *, event_sink=None): ...


class ChatCompletionsProvider:
    def __init__(self, config, *, transport=None, sleep=time.sleep, output_schema=None):
        self.config = config
        self.transport, self.sleep = transport, sleep
        self.output_schema = output_schema

    def complete(self, messages, validator, *, event_sink=None):
        config = self.config
        key = os.environ.get(config.key_env, "").strip()
        if not key and config.credential_file:
            from .credentials import read_key
            try:key = read_key(config.credential_file)
            except (ValueError,OSError):raise LLMError("missing_api_key") from None
        if not key: raise LLMError("missing_api_key")
        payload = {"model": config.model, "messages": messages, "stream": False, config.output_token_parameter: config.max_output_tokens}
        if config.temperature is not None and not (config.protocol_profile == "deepseek" and config.thinking_mode == "enabled"): payload["temperature"] = config.temperature
        if config.thinking_mode is not None: payload["thinking"] = {"type": config.thinking_mode}
        if config.response_format == "json_schema":
            if self.output_schema is not None:
                schema = self.output_schema
            elif config.schema_mode == "screening_v2":
                from .llm_screening import OUTPUT_SCHEMA as schema
            else:schema = OUTPUT_SCHEMA
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "notice_analysis", "strict": True, "schema": schema}}
        elif config.response_format == "json_object": payload["response_format"] = {"type": "json_object"}
        endpoint = config.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=config.timeout, follow_redirects=False, transport=self.transport, verify=trusted_context()) as client:
            for attempt in range(1, config.retries + 2):
                event = {"request_uid": uuid.uuid4().hex, "started_at": utc_now(), "attempt": attempt,
                         "status_code": None, "error_category": "started", "usage": None,
                         "estimated_cost": None, "currency": config.cost_currency, "elapsed_ms": 0}
                diagnostic = {"request_ok": None, "json_ok": None, "schema_ok": None, "evidence_ok": None,
                              "direct_json_ok":None, "parse_method":None, "parser_version":PARSER_VERSION,
                              "analysis_ok": None, "finish_reason": None, "content_chars": None, "reasoning_chars": None,
                              "error_detail": None, "validation_stage": "transport", "json_style": None,
                              "request_format": config.response_format, "thinking_mode": config.thinking_mode,
                              "temperature_sent": "temperature" in payload, "output_token_parameter": config.output_token_parameter,
                              "max_output_tokens": config.max_output_tokens, "timeout":config.timeout, "retries":config.retries,
                              "schema_mode":config.schema_mode, "protocol_profile":config.protocol_profile}
                event["diagnostic"] = diagnostic
                if event_sink: event_sink(dict(event))  # Persist before spending a request.
                started = time.monotonic()
                error = None
                content = None; data = None
                raw = bytearray()
                try:
                    with client.stream("POST", endpoint, json=payload, headers={"Authorization": "Bearer " + key}) as response:
                        code = event["status_code"] = response.status_code
                        mime = response.headers.get("content-type","").split(";",1)[0].strip().lower()
                        diagnostic["response_mime"] = mime if mime in {"application/json","application/problem+json","text/html","text/plain","text/event-stream","application/octet-stream"} else "unknown"
                        if code != 200:
                            if config.capture_failure_diagnostics:
                                diagnostic.update(validation_stage="api_envelope",validation_error_path="$")
                                for chunk in response.iter_bytes():
                                    raw.extend(chunk)
                                    if len(raw)>8192:
                                        raw = raw[:8192]; diagnostic["error_body_truncated"] = True; break
                                diagnostic["response_bytes"] = len(raw)
                                try:error_body = strict_json(raw)
                                except (ValueError,UnicodeError):error_body = None
                                event["usage"] = usage_values(error_body)
                                known_params = {"model","response_format","thinking","temperature","max_tokens","max_completion_tokens"}
                                if isinstance(error_body,dict) and isinstance(error_body.get("error"),dict):
                                    diagnostic["validation_error_path"] = "$.error"
                                    error_param = error_body["error"].get("param")
                                    if isinstance(error_param,str) and error_param in known_params:
                                        diagnostic.update(provider_error_param=error_param,validation_error_path="$.error.param")
                            category = "authentication" if code == 401 else "forbidden" if code == 403 else "rate_limited" if code == 429 else "server_error" if code >= 500 else "http_error"
                            raise LLMError(category, code == 429 or code in {500, 502, 503, 504})
                        diagnostic["request_ok"] = True
                        diagnostic["validation_stage"] = "api_envelope"
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 1024 * 1024: raise LLMError("response_too_large")
                    diagnostic["response_bytes"] = len(raw)
                    try: body = strict_json(raw)
                    except (ValueError, UnicodeError) as exc:
                        if isinstance(exc,json.JSONDecodeError):diagnostic.update(api_json_error_line=exc.lineno,api_json_error_column=exc.colno)
                        raise LLMError("invalid_api_json") from None
                    if not isinstance(body, dict): raise LLMError("invalid_api_response")
                    event["usage"] = usage_values(body)
                    diagnostic["returned_model_matches_config"] = body.get("model") == config.model if isinstance(body.get("model"), str) else None
                    output_details = body.get("usage", {}).get("completion_tokens_details", {}) if isinstance(body.get("usage"), dict) else {}
                    reasoning_tokens = output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None
                    if type(reasoning_tokens) is int and event["usage"] and 0 <= reasoning_tokens <= event["usage"]["completion_tokens"]:
                        diagnostic["reasoning_tokens"] = reasoning_tokens
                    if event["usage"] and config.input_price_per_million is not None:
                        event["estimated_cost"] = (event["usage"]["prompt_tokens"] * config.input_price_per_million + event["usage"]["completion_tokens"] * config.output_price_per_million) / 1000000
                    choices = body.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): raise LLMError("missing_choices")
                    choice = choices[0]; message = choice.get("message")
                    if not isinstance(message, dict): raise LLMError("missing_message")
                    finish = choice.get("finish_reason")
                    diagnostic["finish_reason"] = finish if isinstance(finish, str) and finish in {"stop", "length", "content_filter", "tool_calls", "function_call", "insufficient_system_resource"} else "unknown"
                    content = message.get("content")
                    diagnostic["content_present"] = content is not None
                    diagnostic["content_chars"] = len(content) if isinstance(content, str) else None
                    diagnostic["reasoning_chars"] = len(message["reasoning_content"]) if isinstance(message.get("reasoning_content"), str) else None
                    diagnostic["validation_stage"] = "final_content"
                    if message.get("refusal"): raise LLMError("model_refusal")
                    if message.get("tool_calls") or message.get("function_call"): raise LLMError("unexpected_tool_call")
                    if choice.get("finish_reason") != "stop": raise LLMError("incomplete_output")
                    if not isinstance(content, str) or not content.strip(): raise LLMError("no_final_text")
                    diagnostic["validation_stage"] = "json"
                    diagnostic["json_ok"] = False
                    diagnostic["json_style"] = "markdown_fence" if content.lstrip().startswith("```") else "plain"
                    try: parsed = parse_model_json(content)
                    except ModelJSONError as exc:
                        diagnostic.update(parse_method=exc.method,direct_json_ok=False,json_error_detail=exc.detail,
                            direct_json_error_line=exc.direct_error_line,direct_json_error_column=exc.direct_error_column)
                        if exc.error_line is not None:
                            diagnostic.update(json_error_line=exc.error_line,json_error_column=exc.error_column)
                        raise LLMError("invalid_json") from None
                    data = parsed.value
                    diagnostic.update(parse_method=parsed.method,direct_json_ok=parsed.method=="direct_json",
                        direct_json_error_line=parsed.direct_error_line,direct_json_error_column=parsed.direct_error_column)
                    diagnostic.update(json_ok=True, validation_stage="schema")
                    try: result = validator(data)
                    except (ValueError, TypeError, KeyError) as exc:
                        if isinstance(exc,(SchemaValidationError,EvidenceLocationError)):
                            diagnostic["validation_error_path"] = exc.path
                        if isinstance(exc, SchemaValidationError):
                            diagnostic.update(schema_error_path=exc.path, missing_schema_fields=list(exc.missing_fields))
                        detail = SCHEMA_DETAILS.get(str(exc), "invalid_schema")
                        stage = validation_stage(detail)
                        diagnostic.update(error_detail=detail, validation_stage=stage)
                        if stage != "unknown_unobservable":diagnostic["schema_ok"] = stage != "schema"
                        if stage == "evidence_location":diagnostic["evidence_ok"] = False
                        elif stage == "fact_support":diagnostic["evidence_ok"] = True
                        raise LLMError("schema_validation", detail=detail) from None
                    diagnostic.update(schema_ok=True, evidence_ok=not any(e.location_status == "not_found" for e in getattr(result, "evidence_validation", ())),
                                      analysis_ok=True, validation_stage="complete")
                    diagnostic["fact_status"] = getattr(result, "fact_status", "not_checked")
                except httpx.TimeoutException: error = LLMError("timeout", True)
                except httpx.RequestError: error = LLMError("connection", True)
                except LLMError as exc: error = exc
                if error:
                    diagnostic["analysis_ok"] = False
                    if diagnostic["request_ok"] is None:diagnostic["request_ok"] = False
                    if config.capture_failure_diagnostics:
                        from .llm_debug import failure_evidence
                        try:expected_id = strict_json(messages[-1]["content"])["untrusted_notice"]["notice_id"]
                        except (ValueError,TypeError,KeyError,IndexError):expected_id = None
                        envelope = bytes(raw).decode("utf-8",errors="replace") if content is None and raw else None
                        diagnostic["failure_evidence"] = failure_evidence(content,data,diagnostic,expected_id,envelope)
                event.update(error_category=error.category if error else None, elapsed_ms=round((time.monotonic() - started) * 1000))
                if event_sink: event_sink(dict(event))
                if error is None: return result
                if error.retryable and attempt <= config.retries:
                    self.sleep(min(2 ** (attempt - 1), 8))
                    continue
                raise error
        raise AssertionError("Unreachable")


def external_url(value):
    parts = urlsplit(value)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def external_text(value, remove_identity_lines=True):
    value = sanitize(value, remove_identity_lines=remove_identity_lines)
    value = re.sub(r"github_pat_[A-Za-z0-9_]{20,}", "[凭据已移除]", value)
    # Public notices often enumerate named students with cohort annotations.
    value = re.sub(r"(学子|学生|同学|[，、\s]|^)[\u4e00-\u9fff]{2,4}(?=[（(](?:20)?\d{2}[\u4e00-\u9fffA-Za-z][^）)\n]{0,17}[）)])",
                   lambda m: m.group(1) + "[身份信息已移除]", value)
    value = re.sub(r"在[^。\n]{1,40}(?:老师|教师)指导下", "在指导教师的指导下", value)
    value = re.sub(r"[（(][^）)\n]{0,50}(?:图[/:：]|文[/:：])[^）)\n]{0,50}[）)]", "", value)
    value = re.sub(r"(?m)^.*(?:一审|二审|三审|撰稿|摄影|责任编辑|作者)[：:].*$", "", value)
    # Group/contact rows are unnecessary for screening and must not leave the machine.
    value = re.sub(r"(?mi)^.*(?:QQ|微信|群号|群聊|姓名|学号).*$", "[联系或身份信息已移除]", value)
    # An award roster can contain unannotated coauthors between cohort-tagged names.
    value = re.sub(r"(?m)^.*[（(](?:20)?\d{2}(?![年级届])[A-Za-z\u4e00-\u9fff][^）)\n]{0,17}[）)].*$", "[名单信息已移除]", value)
    value = re.sub(r"https?://[^\s<>，。；]+", lambda m: external_url(m.group()), value)
    return value


class LLMAnalyzer:
    def __init__(self, config, *, provider=None, event_sink=None):
        self.config, self.model_name = config, config.model
        self.provider = provider or ChatCompletionsProvider(config)
        self.event_sink = event_sink
        custom = Path(config.prompt_file).read_text(encoding="utf-8") if config.prompt_file else ""
        if re.search(r"\b(?:sk-|github_pat_|ghp_)[A-Za-z0-9_-]{20,}", custom): raise ValueError("Custom prompt contains credential-like data")
        if config.schema_mode == "screening_v2":
            from . import llm_screening as contract
        else:
            from . import llm_schema as contract
        self.contract = contract
        self.prompt = contract.SYSTEM_PROMPT + ("\n补充任务说明：\n" + custom if custom else "")
        self.prompt_version = contract.PROMPT_VERSION + ":" + fingerprint(self.prompt)[:12]
        self.schema_version = contract.SCHEMA_VERSION + ":" + fingerprint(contract.OUTPUT_SCHEMA)[:12]
        implementation = [files("cdu_radar").joinpath(name).read_text(encoding="utf-8").replace("\r\n", "\n")
                          for name in ["llm.py", "tls.py", "llm_json.py", "llm_schema.py", "llm_screening.py", "llm_semantics.py", "screening_dates.py", "evidence.py", "analysis_service.py", "analysis_models.py", "rule_analysis.py", "rules.json", "privacy.py"]]
        self.version = ("llm-v2:" if config.schema_mode == "screening_v2" else "llm-v1:") + fingerprint([config.identity(), self.prompt, self.prompt_version, self.schema_version, contract.OUTPUT_SCHEMA, implementation])[:24]

    def prepare(self, notice):
        supplied = replace(notice, title=external_text(notice.title, False), content=external_text(notice.content),
            sources=tuple(external_text(s) for s in notice.sources), content_source=external_text(notice.content_source) if notice.content_source else None,
            attachments=tuple(InputLink(external_text(a.name), external_url(a.url)) for a in notice.attachments),
            body_links=tuple(InputLink(external_text(a.name), external_url(a.url)) for a in notice.body_links))
        if self.config.schema_mode == "detailed_v1" and not re.sub(r"(?m)^\[[^\]\r\n]*已移除\]\s*$", "", supplied.content).strip(): raise LLMError("empty_body")
        values = {name: getattr(supplied, name) for name in ["notice_id", "title", "content", "canonical_url", "published_at", "sources", "content_source"]}
        values.update(body_links=[asdict(a) for a in supplied.body_links], attachments=[asdict(a) for a in supplied.attachments],
                      attachments_content_available=False, link_query_parameters_removed=True)
        messages = [{"role": "system", "content": self.prompt + "\nJSON Schema：\n" + json.dumps(self.contract.OUTPUT_SCHEMA, ensure_ascii=False)},
                    {"role": "user", "content": json.dumps({"untrusted_notice": values}, ensure_ascii=False)}]
        if sum(len(m["content"]) for m in messages) > self.config.max_input_chars: raise LLMError("input_too_long")
        return supplied, messages

    def analyze(self, notice, *, reference_time):
        supplied, messages = self.prepare(notice)
        sink = (lambda event: self.event_sink(notice, self, event)) if self.event_sink else None
        coercer = self.contract.coerce_screening if self.config.schema_mode == "screening_v2" else coerce_result
        def validator(payload):
            result = coercer(payload, notice, supplied, self, reference_time)
            # Include the existing original-input check in provider diagnostics too.
            # Redaction markers are not factual quotes from the original notice.
            from .analysis_service import validate_output
            for index,e in enumerate(result.evidence):
                if e.kind == "fact" and locate(notice,e.quote).status not in {"valid","normalized_match"}:
                    raise EvidenceLocationError(f"$.evidence[{index}].quote")
            validate_output(notice,result,self)
            return result
        return self.provider.complete(messages, validator, event_sink=sink)

    def validate_cached(self, notice, result):
        supplied, _ = self.prepare(notice)
        if self.config.schema_mode == "screening_v2":
            from .llm_screening import validate_cached_screening
            return validate_cached_screening(notice, supplied, result, self)
        payload = {name: getattr(result, name) for name in TEXT_FIELDS}
        for name in ["category", "relevance_level", "eligibility_status", "ai_use_policy", "student_opportunity", "needs_manual_review", "status_basis"]:
            payload[name] = getattr(result, name)
        payload.update(candidate_decision=result.base_decision, analysis_reason=list(result.analysis_reason),
                       verification_items=[e.reason for e in result.evidence if e.field == "verification_items"],
                       evidence=[asdict(e) for e in result.evidence if e.field != "verification_items"])
        coerce_result(payload, notice, supplied, self, result.reference_time)

    def validate_dates(self, notice, result):
        if self.config.schema_mode == "screening_v2":
            from .screening_dates import validate_screening_dates
            return validate_screening_dates(notice, result)
        validate_llm_dates(notice, result)
