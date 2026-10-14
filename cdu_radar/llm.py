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
from .models import utc_now
from .privacy import sanitize


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
}


class LLMError(ValueError):
    def __init__(self, category, retryable=False, detail=None):
        self.category, self.retryable, self.detail = category, retryable, detail
        super().__init__(category)  # Never retain API exception/response text.


@dataclass(frozen=True)
class LLMConfig:
    base_url: str
    model: str
    key_env: str = "CDU_LLM_API_KEY"
    temperature: float | None = None
    thinking_mode: str | None = None
    timeout: float = 60
    retries: int = 1
    max_input_chars: int = 20000
    max_output_tokens: int = 4096
    batch_limit: int = 3
    response_format: str = "json_object"
    prompt_file: str | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cost_currency: str | None = None

    def __post_init__(self):
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
        for name in ["retries", "max_input_chars", "max_output_tokens", "batch_limit"]:
            value = getattr(self, name)
            if type(value) is not int or value < (0 if name == "retries" else 1): raise ValueError("Invalid LLM limit")
        if self.retries > 3 or self.batch_limit > 100: raise ValueError("Retry/batch safety bound exceeded")
        if self.response_format not in {"json_schema", "json_object", "none"}: raise ValueError("Invalid response format mode")
        if self.thinking_mode not in {None, "enabled", "disabled"}: raise ValueError("Invalid optional thinking mode")
        priced = [self.input_price_per_million is not None, self.output_price_per_million is not None]
        if any(priced) and (not all(priced) or not self.cost_currency): raise ValueError("Both prices and currency are required")

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        base, model = env.get("CDU_LLM_BASE_URL", ""), env.get("CDU_LLM_MODEL", "")
        if not base or not model: raise ValueError("Set CDU_LLM_BASE_URL and CDU_LLM_MODEL")
        values = {"base_url": base, "model": model}
        converters = {"temperature": float, "timeout": float, "retries": int, "max_input_chars": int,
                      "max_output_tokens": int, "batch_limit": int, "input_price_per_million": float,
                      "output_price_per_million": float}
        for name in cls.__dataclass_fields__:
            if name in {"base_url", "model"}: continue
            raw = env.get("CDU_LLM_" + name.upper())
            if raw is None or raw == "": continue
            try: values[name] = converters[name](raw) if name in converters else raw
            except (ValueError, TypeError): raise ValueError("Invalid CDU_LLM_" + name.upper()) from None
        return cls(**values)

    def identity(self):
        names = ["base_url", "model", "temperature", "thinking_mode", "max_input_chars", "max_output_tokens", "response_format"]
        return {name: getattr(self, name) for name in names}


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result: raise ValueError("Duplicate JSON key")
            result[key] = value
        return result
    return json.loads(text, object_pairs_hook=pairs)


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
    def __init__(self, config, *, transport=None, sleep=time.sleep):
        self.config = config
        self.transport, self.sleep = transport, sleep

    def complete(self, messages, validator, *, event_sink=None):
        config = self.config
        key = os.environ.get(config.key_env, "").strip()
        if not key: raise LLMError("missing_api_key")
        payload = {"model": config.model, "messages": messages, "stream": False, "max_tokens": config.max_output_tokens}
        if config.temperature is not None: payload["temperature"] = config.temperature
        if config.thinking_mode is not None: payload["thinking"] = {"type": config.thinking_mode}
        if config.response_format == "json_schema":
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "notice_analysis", "strict": True, "schema": OUTPUT_SCHEMA}}
        elif config.response_format == "json_object": payload["response_format"] = {"type": "json_object"}
        endpoint = config.base_url.rstrip("/") + "/chat/completions"
        with httpx.Client(timeout=config.timeout, follow_redirects=False, transport=self.transport) as client:
            for attempt in range(1, config.retries + 2):
                event = {"request_uid": uuid.uuid4().hex, "started_at": utc_now(), "attempt": attempt,
                         "status_code": None, "error_category": "started", "usage": None,
                         "estimated_cost": None, "currency": config.cost_currency, "elapsed_ms": 0}
                if event_sink: event_sink(dict(event))  # Persist before spending a request.
                started = time.monotonic()
                error = None
                try:
                    with client.stream("POST", endpoint, json=payload, headers={"Authorization": "Bearer " + key}) as response:
                        code = event["status_code"] = response.status_code
                        if code != 200:
                            category = "authentication" if code == 401 else "forbidden" if code == 403 else "rate_limited" if code == 429 else "server_error" if code >= 500 else "http_error"
                            raise LLMError(category, code == 429 or code in {500, 502, 503, 504})
                        raw = bytearray()
                        for chunk in response.iter_bytes():
                            raw.extend(chunk)
                            if len(raw) > 1024 * 1024: raise LLMError("response_too_large")
                    try: body = strict_json(raw)
                    except (ValueError, UnicodeError): raise LLMError("invalid_api_json") from None
                    if not isinstance(body, dict): raise LLMError("invalid_api_response")
                    event["usage"] = usage_values(body)
                    if event["usage"] and config.input_price_per_million is not None:
                        event["estimated_cost"] = (event["usage"]["prompt_tokens"] * config.input_price_per_million + event["usage"]["completion_tokens"] * config.output_price_per_million) / 1000000
                    choices = body.get("choices")
                    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict): raise LLMError("missing_choices")
                    choice = choices[0]; message = choice.get("message")
                    if not isinstance(message, dict): raise LLMError("missing_message")
                    if message.get("refusal"): raise LLMError("model_refusal")
                    if message.get("tool_calls") or message.get("function_call"): raise LLMError("unexpected_tool_call")
                    if choice.get("finish_reason") != "stop": raise LLMError("incomplete_output")
                    content = message.get("content")
                    if not isinstance(content, str) or not content.strip(): raise LLMError("no_final_text")
                    try: data = strict_json(content)
                    except (ValueError, UnicodeError): raise LLMError("invalid_json") from None
                    try: result = validator(data)
                    except (ValueError, TypeError, KeyError) as exc:
                        raise LLMError("schema_validation", detail=SCHEMA_DETAILS.get(str(exc), "invalid_schema")) from None
                except httpx.TimeoutException: error = LLMError("timeout", True)
                except httpx.RequestError: error = LLMError("connection", True)
                except LLMError as exc: error = exc
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
    value = re.sub(r"https?://[^\s<>，。；]+", lambda m: external_url(m.group()), value)
    return value


class LLMAnalyzer:
    def __init__(self, config, *, provider=None, event_sink=None):
        self.config, self.model_name = config, config.model
        self.provider = provider or ChatCompletionsProvider(config)
        self.event_sink = event_sink
        custom = Path(config.prompt_file).read_text(encoding="utf-8") if config.prompt_file else ""
        if re.search(r"\b(?:sk-|github_pat_|ghp_)[A-Za-z0-9_-]{20,}", custom): raise ValueError("Custom prompt contains credential-like data")
        self.prompt = SYSTEM_PROMPT + ("\n补充任务说明：\n" + custom if custom else "")
        self.prompt_version = PROMPT_VERSION + ":" + fingerprint(self.prompt)[:12]
        self.schema_version = SCHEMA_VERSION + ":" + fingerprint(OUTPUT_SCHEMA)[:12]
        implementation = [files("cdu_radar").joinpath(name).read_text(encoding="utf-8").replace("\r\n", "\n")
                          for name in ["llm.py", "llm_schema.py", "analysis_service.py", "analysis_models.py", "privacy.py"]]
        self.version = "llm-v1:" + fingerprint([config.identity(), self.prompt, PROMPT_VERSION, SCHEMA_VERSION, OUTPUT_SCHEMA, implementation])[:24]

    def prepare(self, notice):
        supplied = replace(notice, title=external_text(notice.title, False), content=external_text(notice.content),
            sources=tuple(external_text(s) for s in notice.sources), content_source=external_text(notice.content_source) if notice.content_source else None,
            attachments=tuple(InputLink(external_text(a.name), external_url(a.url)) for a in notice.attachments),
            body_links=tuple(InputLink(external_text(a.name), external_url(a.url)) for a in notice.body_links))
        if not re.sub(r"(?m)^\[[^\]\r\n]*已移除\]\s*$", "", supplied.content).strip(): raise LLMError("empty_body")
        values = {name: getattr(supplied, name) for name in ["notice_id", "title", "content", "canonical_url", "published_at", "sources", "content_source"]}
        values.update(body_links=[asdict(a) for a in supplied.body_links], attachments=[asdict(a) for a in supplied.attachments],
                      attachments_content_available=False, link_query_parameters_removed=True)
        messages = [{"role": "system", "content": self.prompt + "\nJSON Schema：\n" + json.dumps(OUTPUT_SCHEMA, ensure_ascii=False)},
                    {"role": "user", "content": json.dumps({"untrusted_notice": values}, ensure_ascii=False)}]
        if sum(len(m["content"]) for m in messages) > self.config.max_input_chars: raise LLMError("input_too_long")
        return supplied, messages

    def analyze(self, notice, *, reference_time):
        supplied, messages = self.prepare(notice)
        sink = (lambda event: self.event_sink(notice, self, event)) if self.event_sink else None
        return self.provider.complete(messages, lambda payload: coerce_result(payload, notice, supplied, self, reference_time), event_sink=sink)

    def validate_cached(self, notice, result):
        supplied, _ = self.prepare(notice)
        payload = {name: getattr(result, name) for name in TEXT_FIELDS}
        for name in ["category", "relevance_level", "eligibility_status", "ai_use_policy", "student_opportunity", "needs_manual_review", "status_basis"]:
            payload[name] = getattr(result, name)
        payload.update(candidate_decision=result.base_decision, analysis_reason=list(result.analysis_reason),
                       verification_items=[e.reason for e in result.evidence if e.field == "verification_items"],
                       evidence=[asdict(e) for e in result.evidence if e.field != "verification_items"])
        coerce_result(payload, notice, supplied, self, result.reference_time)

    def validate_dates(self, notice, result):
        validate_llm_dates(notice, result)
