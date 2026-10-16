import logging
import time
from dataclasses import replace
from urllib.parse import urlsplit

from .adapters import ARTICLE_PATH, VsbAdapter, article_adapter
from .models import ParseError, RadarError, Source, utc_now
from .sources import content_source
from .http import RequestBudgetExceeded

LOG = logging.getLogger(__name__)


def collect(store, fetcher, sources: list[Source], *, limit: int = 3, max_pages: int = 1, stop_event=None, skip_known=False) -> list[dict]:
    if limit <= 0 or max_pages <= 0:
        raise ValueError("limit and max_pages must be positive")
    summaries = []
    for source in sources:
        if stop_event is not None and stop_event.is_set():break
        started = utc_now()
        monotonic_start = time.monotonic()
        counts = dict(inserted=0, updated=0, unchanged=0, failed=0, pages_read=0, parse_failures=0, duplicates=0)
        errors = []
        failures = []
        fetcher.source_id = source.id
        try:
            # Attempt known column connectivity even when its adapter is pending.
            if not source.column_url:
                raise RadarError("No verified column URL configured")
            adapter = VsbAdapter(source.adapter)
            next_url = source.column_url
            visited_pages, seen_articles = set(), set()
            for _ in range(max_pages):
                if stop_event is not None and stop_event.is_set():break
                if next_url is None or next_url in visited_pages or len(seen_articles) >= limit:
                    break
                visited_pages.add(next_url)
                html, final_url = fetcher.fetch(next_url)
                counts["pages_read"] += 1
                links = adapter.parse_list(html, final_url)
                for link in links:
                    if stop_event is not None and stop_event.is_set():break
                    if link.canonical_url in seen_articles:
                        counts["duplicates"] += 1
                        continue
                    if skip_known:
                        known = store.db.execute('SELECT id FROM notices WHERE canonical_url=?', (link.canonical_url,)).fetchone()
                        if known:
                            with store.db:
                                store.record_discovery(known['id'],source.id,utc_now(),link.published_at)
                            counts['duplicates'] += 1
                            continue
                    if len(seen_articles) >= limit:
                        break
                    seen_articles.add(link.canonical_url)
                    canonical = link.canonical_url
                    try:
                        # Reject unsupported/unconfigured content before fetching it.
                        if not ARTICLE_PATH.fullmatch(urlsplit(link.canonical_url).path):
                            raise ParseError(f"Unsupported article path: {link.canonical_url}")
                        content_source(link.canonical_url)
                        detail, canonical = fetcher.fetch(link.canonical_url)
                        if not ARTICLE_PATH.fullmatch(urlsplit(canonical).path):
                            raise ParseError(f"Unexpected final article path: {canonical}")
                        unit_id, _ = content_source(canonical)
                        article = article_adapter(canonical).parse_article(detail, canonical)
                        if (article.published_at and link.published_at and article.published_at != link.published_at
                                and urlsplit(source.column_url).hostname == urlsplit(canonical).hostname):
                            raise ParseError(f"Publication date conflict: list={link.published_at}, article={article.published_at}")
                        article = replace(article, published_at=article.published_at or link.published_at, content_source_id=unit_id)
                        if stop_event is not None and stop_event.is_set():break
                        result = store.save_notice(source.id, canonical, article, discovery_published_at=link.published_at)
                        counts[result] += 1
                        counts["duplicates"] += result in {"updated","unchanged"}
                    except RequestBudgetExceeded:
                        raise
                    except RadarError as exc:
                        store.fail_notice(link.canonical_url, exc)
                        if canonical != link.canonical_url:
                            store.fail_notice(canonical, exc)
                        counts["failed"] += 1
                        counts["parse_failures"] += isinstance(exc,ParseError)
                        errors.append(str(exc))
                        failures.append(exc)
                        LOG.error("source=%s category=%s scope=%s article_error=%s", source.id,exc.category,exc.scope,exc)
                next_url = adapter.next_page(html, final_url, source.column_url) if len(seen_articles)<limit and counts["pages_read"]<max_pages else None
        except RequestBudgetExceeded:
            # A local scheduling limit is not evidence that the source failed.
            summaries.append({'source':source.id, **counts, 'success':False, 'error_category':'budget'})
            break
        except RadarError as exc:
            counts["failed"] += 1
            counts["parse_failures"] += isinstance(exc,ParseError)
            errors.append(str(exc))
            failures.append(exc)
            LOG.error("source=%s category=%s scope=%s collection_error=%s", source.id,exc.category,exc.scope,exc)
        error = " | ".join(errors) or None
        counts["elapsed_seconds"] = round(time.monotonic()-monotonic_start,3)
        failure = failures[0] if failures else None
        store.finish_run(source.id, started, counts, error, failure=failure)
        summaries.append({"source": source.id, **counts, "success":not errors,"error": error,
                          "error_category":failure.category if failure else None,
                          "error_scope":failure.scope if failure else None,
                          "retryable":failure.retryable if failure else True})
    return summaries
