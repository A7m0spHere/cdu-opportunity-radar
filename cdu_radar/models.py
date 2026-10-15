from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit


class RadarError(Exception):
    """Expected collection failure; safe to report and isolate."""

    category = "configuration"
    scope = "configuration"
    retryable = False


class ParseError(RadarError):
    category = "html_parse"
    scope = "parser"


class SourceUnavailable(RadarError):
    """A known upstream maintenance response, even when HTTP reports 200."""
    category = "source_maintenance"
    scope = "source"
    retryable = True


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def official_url(url: str) -> str:
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError as exc:
        raise RadarError(f"Malformed official URL: {url}") from exc
    if (parts.scheme not in {"http", "https"} or
            not (host == "cdu.edu.cn" or host.endswith(".cdu.edu.cn")) or
            parts.username or parts.password or port not in {None, 80, 443}):
        raise RadarError(f"URL outside supported official CDU sites: {url}")
    # Keep meaningful query parameters; remove only fragments.
    return urlunsplit((parts.scheme, parts.netloc.lower(), parts.path or "/", parts.query, ""))


@dataclass(frozen=True)
class Source:
    id: str
    college: str
    homepage: str | None
    column_name: str | None
    column_url: str | None
    column_type: str | None
    adapter: str | None
    status: str
    last_verified_at: str | None
    directory_checked_at: str
    note: str = ""
    unit_id: str | None = None
    unit_type: str | None = None
    official_domain: str | None = None
    discovered_from: str | None = None
    last_checked_at: str | None = None
    accessible: bool | None = None
    articles_found: bool | None = None
    parser_supported: bool = False
    error_category: str | None = None
    error_reason: str | None = None
    content_adapter: str | None = None


@dataclass(frozen=True)
class NoticeLink:
    title: str
    canonical_url: str
    published_at: str | None


@dataclass(frozen=True)
class Article:
    title: str
    published_at: str | None
    content: str
    links: tuple = ()
    attachments: tuple = ()
    content_source_id: str | None = None


@dataclass(frozen=True)
class ArticleLink:
    name: str
    url: str
