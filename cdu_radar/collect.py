import logging
from dataclasses import replace

from .adapters import VsbAdapter, article_adapter
from .models import RadarError, Source, utc_now

LOG = logging.getLogger(__name__)


def collect(store, fetcher, sources: list[Source], *, limit: int = 3) -> list[dict]:
    if limit <= 0:
        raise ValueError("limit must be positive")
    summaries = []
    for source in sources:
        started = utc_now()
        counts = dict(inserted=0, updated=0, unchanged=0, failed=0)
        errors = []
        fetcher.source_id = source.id
        try:
            # Attempt known column connectivity even when its adapter is pending.
            if not source.column_url:
                raise RadarError("No verified column URL configured")
            html, final_url = fetcher.fetch(source.column_url)
            links = VsbAdapter(source.adapter).parse_list(html, final_url)
            for link in links[:limit]:
                try:
                    detail, canonical = fetcher.fetch(link.canonical_url)
                    article = article_adapter(canonical).parse_article(detail, canonical)
                    if article.published_at and link.published_at and article.published_at != link.published_at:
                        raise RadarError(f"Publication date conflict: list={link.published_at}, article={article.published_at}")
                    article = replace(article, published_at=article.published_at or link.published_at)
                    result = store.save_notice(source.id, canonical, article)
                    counts[result] += 1
                except RadarError as exc:
                    counts["failed"] += 1
                    errors.append(str(exc))
                    LOG.error("source=%s article_error=%s", source.id, exc)
        except RadarError as exc:
            counts["failed"] += 1
            errors.append(str(exc))
            LOG.error("source=%s collection_error=%s", source.id, exc)
        error = " | ".join(errors) or None
        store.finish_run(source.id, started, counts, error)
        summaries.append({"source": source.id, **counts, "error": error})
    return summaries
