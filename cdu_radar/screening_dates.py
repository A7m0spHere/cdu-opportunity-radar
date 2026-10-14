"""Role-specific screening dates: inspect evidence, never manufacture dates."""
import re

from .analysis_models import iso_date
from .evidence import contexts, locate, normalize
from .llm_schema import DATE_ROLES, quoted_dates

DATE_FIELDS = {"registration_deadline", "registration_start_date", "internal_deadline"}


def role(segment):
    return next((name for name, pattern in DATE_ROLES.items() if pattern.search(segment)), None)


def date_reason(notice, field, quote, value):
    match = locate(notice, quote, field)
    if match.status == "not_found":return "not_found"
    if not match.source:return "missing_citation"
    if value is None:return "unknown_date_value"
    try:iso_date(value)
    except ValueError:return "invalid_date"
    if match.source not in {"title", "content"}:return "non_factual_date_source"
    try:dates, ranges = quoted_dates(normalize(quote))
    except ValueError:return "invalid_calendar_in_quote"
    if value not in dates:return "value_not_in_quote_or_year_missing"
    surrounding = contexts(notice, quote)
    if any(re.search(r"发布时间|发布日期|发布于|暂定|预计|拟定|可能", c) for c in surrounding):return "metadata_or_tentative_date"
    relevant = []
    for c in surrounding:
        for segment in re.split(r"[，,。；\n]", c):
            try:seen, pairs = quoted_dates(segment)
            except ValueError:return "invalid_calendar_in_context"
            if value in seen:relevant.append((segment, seen, pairs))
    if not relevant:return "missing_date_context"
    for segment, seen, pairs in relevant:
        if pairs and re.search(r"报名时间|报名期间", segment) and not re.search(r"校内|学院|推荐|选拔", segment):
            endpoint = 0 if field == "registration_start_date" else 1 if field == "registration_deadline" else None
            if endpoint is None or value not in {p[endpoint] for p in pairs}:return "wrong_range_endpoint"
        elif role(segment) != field:return "wrong_date_role"
        elif len(seen) > 1:return "multiple_dates"
    return "supported"


def conflicting_dates(notice, field, value):
    """Look for conflicts only; unquoted dates never populate a trusted field."""
    mentioned = set()
    # Preserve clause boundaries before whitespace normalization.
    for raw in re.split(r"[，,。；\n]", notice.content):
        segment = normalize(raw)
        try:dates, pairs = quoted_dates(segment)
        except ValueError:
            if role(segment) == field:return True
            continue
        if pairs and re.search(r"报名时间|报名期间", segment) and not re.search(r"校内|学院|推荐|选拔", segment):
            endpoint = 0 if field == "registration_start_date" else 1 if field == "registration_deadline" else None
            if endpoint is not None:mentioned.update(p[endpoint] for p in pairs)
        elif role(segment) == field:
            if not dates and re.search(r"\d{1,2}月\d{1,2}日", segment):return True
            mentioned.update(dates)
    return bool(mentioned - {value})


def validate_screening_dates(notice, result):
    for field in DATE_FIELDS:
        value = getattr(result, field)
        if value is None:continue
        facts = [e for e in result.evidence if e.field == field and e.kind == "fact" and e.value is not None]
        if not facts or any(e.value != value or date_reason(notice, field, e.quote, e.value) != "supported" for e in facts):
            raise ValueError("Date evidence must preserve quoted value and purpose")
        if conflicting_dates(notice, field, value):raise ValueError("Multiple dates cannot become a single deadline/start date")
