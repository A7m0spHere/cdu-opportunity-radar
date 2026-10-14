"""Validated analysis and annotation contracts, independent of raw Notice."""
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import re
from typing import Protocol
from urllib.parse import urlsplit

SHANGHAI = timezone(timedelta(hours=8), "Asia/Shanghai")
CATEGORIES = {"competition","hackathon","workshop","lecture","project_recruitment","other_activity","news","administrative","unknown"}
RELEVANCE = {"direct_ai","ai_applicable","unrelated","uncertain"}
ELIGIBILITY = {"eligible","ineligible","uncertain"}
POLICIES = {"explicitly_allowed","partially_allowed","explicitly_prohibited","unspecified"}
STATUSES = {"open","upcoming","closed","unknown"}
REVIEWS = {"pending","approved","rejected"}
DECISIONS = {"retain","exclude","uncertain"}


def choice(value, values, name):
    if not isinstance(value,str) or value not in values:
        raise ValueError(f"Invalid {name}")


def nullable_bool(value, name):
    if value is not None and type(value) is not bool:
        raise ValueError(f"{name} must be bool or null")


def iso_date(value):
    if value is None:return
    if not isinstance(value,str) or not re.fullmatch(r"20\d{2}-\d{2}-\d{2}(?:T\d{2}:\d{2}:\d{2}\+08:00)?",value):
        raise ValueError("Date must have explicit year and canonical Asia/Shanghai precision")
    datetime.fromisoformat(value)


@dataclass(frozen=True)
class Evidence:
    field: str
    kind: str
    quote: str
    reason: str
    value: str | None = None

    def __post_init__(self):
        choice(self.kind,{"fact","inference","unknown"},"evidence kind")
        if any(not isinstance(v,str) for v in [self.field,self.quote,self.reason]):raise ValueError("Evidence must be textual")
        if not self.field or not self.reason or self.kind=="fact" and not self.quote:raise ValueError("Fact evidence requires a nonempty quote")
        if self.value is not None and not isinstance(self.value,str):raise ValueError("Invalid evidence value")


@dataclass(frozen=True)
class InputLink:
    name: str
    url: str

    def __post_init__(self):
        if not isinstance(self.name,str) or not isinstance(self.url,str):raise ValueError("Invalid link")
        parts=urlsplit(self.url)
        if parts.scheme not in {"http","https"} or not parts.hostname or parts.username or parts.password:raise ValueError("Unsafe input link")


@dataclass(frozen=True)
class AnalysisInput:
    notice_id: int
    content_hash: str
    input_hash: str
    title: str
    content: str
    canonical_url: str
    sources: tuple[str,...]
    published_at: str | None
    attachments: tuple[InputLink,...] = ()
    body_links: tuple[InputLink,...] = ()
    content_source: str | None = None

    def __post_init__(self):
        if type(self.notice_id) is not int or self.notice_id<1:raise ValueError("Invalid notice id")
        for h in [self.content_hash,self.input_hash]:
            if not isinstance(h,str) or not re.fullmatch(r"[a-f0-9]{64}",h):raise ValueError("Invalid content/input hash")
        if any(not isinstance(v,str) for v in [self.title,self.content,self.canonical_url]):raise ValueError("Invalid input text")
        if not isinstance(self.sources,tuple) or any(not isinstance(s,str) for s in self.sources):raise ValueError("Invalid sources")
        if any(not isinstance(items,tuple) or any(not isinstance(link,InputLink) for link in items) for items in [self.attachments,self.body_links]):raise ValueError("Invalid input links")
        iso_date(self.published_at)
        if self.content_source is not None and not isinstance(self.content_source,str):raise ValueError("Invalid content publisher")


@dataclass(frozen=True)
class NoticeAnalysis:
    notice_id: int
    content_hash: str
    input_hash: str
    analyzer_version: str
    analyzed_at: str
    category: str
    relevance_level: str
    eligibility_status: str
    ai_use_policy: str
    opportunity_status: str
    analysis_reason: tuple[str,...]
    evidence: tuple[Evidence,...]
    needs_manual_review: bool
    candidate_decision: str = "uncertain"
    base_decision: str = "uncertain"
    event_name: str | None = None
    organizer: str | None = None
    target_audience: str | None = None
    registration_deadline: str | None = None
    internal_deadline: str | None = None
    registration_start_date: str | None = None
    event_start_date: str | None = None
    event_end_date: str | None = None
    internal_event_date: str | None = None
    registration_url: str | None = None
    requirements_summary: str | None = None
    review_status: str = "pending"
    review_reason: str | None = None
    reviewed_at: str | None = None
    model_name: str | None = None
    reference_time: str | None = None
    status_basis: str = "unknown"
    analysis_state: str = "success"
    student_opportunity: bool | None = None

    def __post_init__(self):
        if type(self.notice_id) is not int or self.notice_id<1:raise ValueError("Invalid analysis id")
        for name,values in [("category",CATEGORIES),("relevance_level",RELEVANCE),("eligibility_status",ELIGIBILITY),
                            ("ai_use_policy",POLICIES),("opportunity_status",STATUSES),("review_status",REVIEWS),
                            ("candidate_decision",DECISIONS),("base_decision",DECISIONS),
                            ("status_basis",{"unknown","deadline","explicit_closed","registration_start"}),
                            ("analysis_state",{"success","failed"})]:choice(getattr(self,name),values,name)
        for name in ["registration_deadline","internal_deadline","registration_start_date","event_start_date","event_end_date","internal_event_date"]:iso_date(getattr(self,name))
        if type(self.needs_manual_review) is not bool:raise ValueError("Invalid manual-review flag")
        nullable_bool(self.student_opportunity, "student_opportunity")
        if not isinstance(self.evidence,tuple) or any(not isinstance(e,Evidence) for e in self.evidence):raise ValueError("Invalid evidence")
        if not isinstance(self.analysis_reason,tuple) or any(not isinstance(r,str) for r in self.analysis_reason):raise ValueError("Invalid reasons")
        for name in ["event_name","organizer","target_audience","registration_url","requirements_summary","review_reason","reviewed_at","model_name","reference_time"]:
            v=getattr(self,name)
            if v is not None and not isinstance(v,str):raise ValueError(f"Invalid {name}")
        for value in [self.content_hash,self.input_hash]:
            if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{64}",value):raise ValueError("Invalid analysis hash")
        if not isinstance(self.analyzer_version,str) or not self.analyzer_version or not isinstance(self.analyzed_at,str):raise ValueError("Missing analyzer metadata")
        for value in [self.analyzed_at,self.reference_time,self.reviewed_at]:
            if value is not None and datetime.fromisoformat(value).tzinfo is None:raise ValueError("Analysis timestamps require a timezone")
        if self.registration_url is not None:InputLink("",self.registration_url)

    def to_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,data):
        if not isinstance(data,dict):raise ValueError("Analysis must be an object")
        values=dict(data)
        try:
            if not isinstance(values["evidence"],list) or not isinstance(values["analysis_reason"],list):raise ValueError("Evidence/reasons must be arrays")
            values["evidence"]=tuple(Evidence(**e) for e in values["evidence"])
            values["analysis_reason"]=tuple(values["analysis_reason"])
            return cls(**values)
        except (TypeError,KeyError) as exc:raise ValueError("Invalid analysis schema") from exc


@dataclass(frozen=True)
class ManualLabel:
    notice_id: int
    content_hash: str
    input_hash: str
    label_origin: str
    labeler: str
    worth_recommending: bool | None = None
    student_opportunity: bool | None = None
    relevance_level: str | None = None
    ai_use_policy: str | None = None
    eligibility_status: str | None = None
    deadline_explicit: bool | None = None
    registration_deadline: str | None = None
    internal_deadline: str | None = None
    opportunity_status: str | None = None
    category: str | None = None
    needs_further_check: bool | None = None
    notes: str = ""
    reference_date: str | None = None
    reference_time: str | None = None

    def __post_init__(self):
        if type(self.notice_id) is not int or self.notice_id<1:raise ValueError("Invalid label id")
        choice(self.label_origin,{"human","synthetic"},"label origin")
        if not isinstance(self.labeler,str) or not self.labeler.strip():raise ValueError("Labeler is required")
        for name in ["worth_recommending","student_opportunity","deadline_explicit","needs_further_check"]:nullable_bool(getattr(self,name),name)
        for name,values in [("relevance_level",RELEVANCE),("ai_use_policy",POLICIES),("eligibility_status",ELIGIBILITY),("opportunity_status",STATUSES),("category",CATEGORIES)]:
            if getattr(self,name) is not None:choice(getattr(self,name),values,name)
        iso_date(self.registration_deadline);iso_date(self.internal_deadline)
        iso_date(self.reference_date)
        if self.reference_date is not None and len(self.reference_date)!=10:raise ValueError("Label reference_date must be YYYY-MM-DD")
        if self.reference_time is not None:
            if not isinstance(self.reference_time,str):raise ValueError("Invalid label reference_time")
            point=reference_datetime(self.reference_time)
            if len(self.reference_time)==10:raise ValueError("Label reference_time requires timestamp and timezone")
            if self.reference_date and point.date().isoformat()!=self.reference_date:raise ValueError("Label date/time disagree")
        if not isinstance(self.notes,str):raise ValueError("Invalid label notes")
        for value in [self.content_hash,self.input_hash]:
            if not isinstance(value,str) or not re.fullmatch(r"[a-f0-9]{64}",value):raise ValueError("Invalid label version")


class Analyzer(Protocol):
    version: str
    def analyze(self, notice: AnalysisInput, *, reference_time: datetime) -> NoticeAnalysis: ...


def fingerprint(data) -> str:
    return hashlib.sha256(json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(",",":")).encode()).hexdigest()


def reference_datetime(value=None):
    if value is None:return datetime.now(SHANGHAI)
    if isinstance(value,datetime):
        if value.tzinfo is None:raise ValueError("Analysis reference time must have timezone")
        return value.astimezone(SHANGHAI)
    parsed=datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        if len(value)!=10:raise ValueError("Use YYYY-MM-DD or a timestamp with timezone")
        parsed=parsed.replace(tzinfo=SHANGHAI)
    return parsed.astimezone(SHANGHAI)
