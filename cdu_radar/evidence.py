"""Bounded citation location. No fuzzy, punctuation or semantic matching."""
from dataclasses import asdict, dataclass
import re
import unicodedata


class EvidenceLocationError(ValueError):
    def __init__(self, path):
        super().__init__("Unlocatable evidence")
        self.path = path


@dataclass(frozen=True)
class EvidenceAssessment:
    field: str
    status: str
    source: str | None = None
    start: int | None = None
    end: int | None = None
    support: str = "unverified"
    proposed_value: str | None = None
    location_status: str | None = None

    def __post_init__(self):
        if self.status not in {"valid", "normalized_match", "unsupported", "not_found", "missing", "unverified"}:
            raise ValueError("Invalid evidence assessment")
        if self.location_status is None:object.__setattr__(self, "location_status", self.status)
        if self.location_status not in {"valid", "normalized_match", "not_found", "missing"}:raise ValueError("Invalid evidence location status")
        if self.support not in {"supported", "unverified", "unsupported", "missing"}:
            raise ValueError("Invalid evidence support")
        if self.start is not None and (type(self.start) is not int or type(self.end) is not int or self.start < 0 or self.end <= self.start):
            raise ValueError("Invalid citation offsets")


def sources(notice):
    # Never match across fields, link captions, publication metadata or body boundaries.
    result = [("title", notice.title), ("content", notice.content),
              ("canonical_url", notice.canonical_url), ("published_at", notice.published_at or ""),
              ("content_source", notice.content_source or "")]
    for key in ["sources", "attachments", "body_links"]:
        for index, value in enumerate(getattr(notice, key)):
            result.append((f"{key}:{index}", value if isinstance(value, str) else value.name))
    return result


def normalize(text):
    # NFC only: no case folding, punctuation removal, NFKC or edit-distance matching.
    return unicodedata.normalize("NFC", re.sub(r"\s", "", text))


def normalized_with_offsets(text):
    chars, offsets = [], []
    # Keep original spans for canonical combining sequences and whitespace changes.
    filtered = [(char, i) for i, char in enumerate(text) if not char.isspace()]
    index = 0
    while index < len(filtered):
        end = index + 1
        cluster = filtered[index][0]
        while end < len(filtered) and (unicodedata.combining(filtered[end][0]) or
                unicodedata.normalize("NFC", cluster + filtered[end][0]) != unicodedata.normalize("NFC", cluster) + unicodedata.normalize("NFC", filtered[end][0])):
            cluster += filtered[end][0]
            end += 1
        for char in unicodedata.normalize("NFC", cluster):
            chars.append(char); offsets.append((filtered[index][1], filtered[end-1][1]+1))
        index = end
    return "".join(chars), offsets


def locate(notice, quote, field=""):
    if not quote or not normalize(quote):
        return EvidenceAssessment(field, "missing", support="missing")
    for name, text in sources(notice):
        start = text.find(quote)
        if start >= 0:
            return EvidenceAssessment(field, "valid", name, start, start + len(quote))
    target = normalize(quote)
    for name, text in sources(notice):
        normalized, offsets = normalized_with_offsets(text)
        start = normalized.find(target)
        if start >= 0:
            return EvidenceAssessment(field, "normalized_match", name, offsets[start][0], offsets[start + len(target) - 1][1])
    return EvidenceAssessment(field, "not_found")


def literal(notice, assessment):
    return next((text[assessment.start:assessment.end] for name, text in sources(notice)
                 if name == assessment.source and assessment.start is not None), "")


def contexts(notice, quote):
    """All occurrences in title/body clauses, including surrounding negation/limits."""
    target = normalize(quote)
    if not target:return []
    result = []
    for name, text in sources(notice):
        if name not in {"title", "content"}:continue
        normalized, offsets = normalized_with_offsets(text)
        index = normalized.find(target)
        while index >= 0:
            start, end = offsets[index][0], offsets[index + len(target)-1][1]
            left = max(text.rfind(mark, 0, start) for mark in ["。", "；", "\n"]) + 1
            right = min([p for mark in ["。", "；", "\n"] if (p := text.find(mark, end)) >= 0] or [len(text)])
            result.append(normalize(text[left:right]))
            index = normalized.find(target, index + 1)
    return result


def location_report(notice, evidence):
    return [asdict(locate(notice, e.quote, e.field)) for e in evidence]
