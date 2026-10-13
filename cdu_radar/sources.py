import json
from importlib.resources import files
from urllib.parse import urlsplit

from .models import Source


def load_sources() -> list[Source]:
    rows = json.loads(files("cdu_radar").joinpath("sources.json").read_text(encoding="utf-8"))
    return [Source(**row) for row in rows]


def trusted_hosts() -> set[str]:
    return {host for source in load_sources() for url in [source.homepage, source.column_url]
            if url and (host := urlsplit(url).hostname)}


def content_source(url: str) -> tuple[str, str]:
    """Return a configured unit and article template, never infer from discovery."""
    from .models import ParseError
    host = urlsplit(url).hostname
    matching = [s for s in load_sources() if s.homepage and urlsplit(s.homepage).hostname == host]
    if len({s.unit_id or s.id for s in matching})>1:
        raise ParseError(f"Ambiguous publishing unit on shared official domain: {host}")
    for source in matching:
        template = source.content_adapter or source.adapter
        if template:
            return source.unit_id or source.id, template
    raise ParseError(f"No verified article adapter for host: {host}")
