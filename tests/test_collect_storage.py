from dataclasses import replace

import httpx

from cdu_radar.collect import collect
from cdu_radar.http import Fetcher
from cdu_radar.models import Article


def transport_for(sample, *, fail_computer=False, article_fail=False):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if fail_computer and request.url.host == "computer.cdu.edu.cn":
            raise httpx.ConnectError("mock network failure", request=request)
        kind = "computer" if request.url.host == "computer.cdu.edu.cn" else "business"
        name = kind + ("_article.html" if "/info/" in request.url.path else "_list.html")
        if article_fail and "/info/" in request.url.path:
            return httpx.Response(200, text="<html>页面结构已经变化</html>")
        return httpx.Response(200, content=sample(name), headers={"content-type":"text/html"})
    return httpx.MockTransport(handler)


def test_repeated_collection_is_idempotent(store, sources, sample):
    with Fetcher(transport=transport_for(sample), interval=0, event_sink=store.request_event) as fetcher:
        first = collect(store, fetcher, sources, limit=1)
        second = collect(store, fetcher, sources, limit=1)
    assert sum(r["inserted"] for r in first) == 2
    assert sum(r["inserted"] for r in second) == 0
    assert sum(r["unchanged"] for r in second) == 2
    assert store.db.execute("SELECT count(*) FROM notices").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM collection_runs").fetchone()[0] == 4
    assert store.db.execute("SELECT count(*) FROM request_events").fetchone()[0] == 10


def test_failure_isolated_and_no_blank_notices(store, sources, sample):
    with Fetcher(transport=transport_for(sample, fail_computer=True), retries=0, interval=0) as fetcher:
        results = collect(store, fetcher, sources, limit=1)
    by_id = {r["source"]: r for r in results}
    assert by_id["computer"]["failed"] == 1
    assert by_id["business"]["inserted"] == 1
    assert store.db.execute("SELECT status FROM sources WHERE id='computer'").fetchone()[0] == "failed"
    assert store.db.execute("SELECT count(*) FROM notices").fetchone()[0] == 1


def test_parse_failure_does_not_destroy_previous_notice(store, sources, sample):
    with Fetcher(transport=transport_for(sample), interval=0) as fetcher:
        collect(store, fetcher, sources, limit=1)
    with Fetcher(transport=transport_for(sample, article_fail=True), interval=0) as fetcher:
        result = collect(store, fetcher, sources, limit=1)
    assert all(r["failed"] == 1 for r in result)
    assert len(store.feed_rows(10)) == 2


def test_updates_preserve_first_seen_and_hash(store):
    url = "https://computer.cdu.edu.cn/info/1/1.htm"
    article = Article("标题", "2026-10-01", "这是第一次成功解析的通知正文")
    assert store.save_notice("computer", url, article, "2026-10-01T01:00:00+00:00") == "inserted"
    first = dict(store.feed_rows(1)[0])
    assert store.save_notice("computer", url, article, "2026-10-02T01:00:00+00:00") == "unchanged"
    unchanged = dict(store.feed_rows(1)[0])
    assert unchanged["updated_at"] == first["updated_at"]
    assert unchanged["last_seen_at"] != first["last_seen_at"]
    changed = replace(article, content="这是修订之后的通知正文", published_at=None)
    assert store.save_notice("business", url, changed, "2026-10-03T01:00:00+00:00") == "updated"
    final = dict(store.feed_rows(1)[0])
    assert final["id"] == first["id"]
    assert final["discovered_at"] == first["discovered_at"]
    assert final["published_at"] == "2026-10-01"
    assert final["application_deadline"] is None
    assert final["content_hash"] != first["content_hash"]
    assert len(store.origins(final["id"])) == 2


def test_sync_does_not_erase_failure_state(store, sources):
    counts = dict(inserted=0,updated=0,unchanged=0,failed=1)
    store.finish_run("computer", "2026-10-01T00:00:00+00:00", counts, "network failure")
    store.sync_sources(sources)
    row = store.db.execute("SELECT * FROM sources WHERE id='computer'").fetchone()
    assert row["status"] == "failed" and row["last_error"] == "network failure"


def test_date_conflict_is_not_silently_stored(store, sources, sample):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        name = "computer_article.html" if "/info/" in request.url.path else "computer_list.html"
        body = sample(name).replace(b"2026-09-14", b"2026-09-15") if "list" in name else sample(name)
        return httpx.Response(200, content=body)
    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        results = collect(store, fetcher, [s for s in sources if s.id == "computer"], limit=1)
    assert "Publication date conflict" in results[0]["error"]
    assert store.feed_rows(10) == []
