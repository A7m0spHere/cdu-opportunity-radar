import httpx
import pytest

from cdu_radar.http import FetchError, Fetcher
from cdu_radar.models import RadarError

URL = "https://computer.cdu.edu.cn/cxcy/xkjs.htm"


def test_timeout_retries_bounded_and_recorded():
    events = []
    requests = []
    def handler(request):
        requests.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        raise httpx.ReadTimeout("mock timeout", request=request)
    with Fetcher(transport=httpx.MockTransport(handler), interval=0, sleep=lambda _: None,
                 event_sink=lambda *args: events.append(args)) as fetcher:
        with pytest.raises(FetchError, match="after 3 attempt"):
            fetcher.fetch(URL)
    assert requests.count(URL) == 3
    assert len(events) == 4
    assert events[-1][3:6] == (3, None, "ReadTimeout")


@pytest.mark.parametrize("status", [401, 403, 429])
def test_access_denied_not_retried(status):
    requests = []
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(404 if request.url.path == "/robots.txt" else status)
    with Fetcher(transport=httpx.MockTransport(handler), interval=0, sleep=lambda _: None) as fetcher:
        with pytest.raises(FetchError, match=f"HTTP {status}"):
            fetcher.fetch(URL)
    assert len(requests) == 2


def test_server_retry_then_success():
    count = 0
    def handler(request):
        nonlocal count
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        count += 1
        return httpx.Response(503 if count == 1 else 200, text="<html>成功</html>", headers={"content-type": "text/html"})
    with Fetcher(transport=httpx.MockTransport(handler), interval=0, sleep=lambda _: None) as fetcher:
        assert "成功" in fetcher.fetch(URL)[0].decode()
    assert count == 2


def test_disallow_stops_page_request():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text="User-agent: *\nDisallow: /cxcy/\n")
    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        with pytest.raises(FetchError, match="disallows"):
            fetcher.fetch(URL)
    assert requests == ["/robots.txt"]


def test_robots_failure_cached_and_page_not_requested():
    requests = []
    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(503)
    with Fetcher(transport=httpx.MockTransport(handler), retries=0, interval=0) as fetcher:
        for _ in range(2):
            with pytest.raises(FetchError, match="robots check failed"):
                fetcher.fetch(URL)
    assert requests == ["/robots.txt"]


def test_html_robots_no_rules_warns(caplog):
    def handler(request):
        return httpx.Response(200, text="<html>页面正在修复中</html>", headers={"content-type": "text/html"})
    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        fetcher.fetch(URL)
    assert "robots_no_rules" in caplog.text


def test_rate_limit_applies_to_all_requests_and_crawl_delay():
    state = [0.0]
    waits = []
    def sleep(seconds):
        state[0] += seconds
        waits.append(seconds)
    def handler(request):
        text = "User-agent: *\nCrawl-delay: 5\n" if request.url.path == "/robots.txt" else "<html>正文</html>"
        return httpx.Response(200, text=text, headers={"content-type": "text/html"})
    with Fetcher(transport=httpx.MockTransport(handler), interval=2, sleep=sleep, clock=lambda: state[0]) as fetcher:
        fetcher.fetch(URL)
        fetcher.fetch(URL)
    assert waits == [5, 5]


def test_redirect_target_is_checked_before_request():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(302, headers={"Location": "https://evil.test/a"})
    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        with pytest.raises(RadarError, match="outside supported"):
            fetcher.fetch(URL)
    assert len(calls) == 2


def test_cross_college_redirect_checks_new_robots():
    calls = []
    def handler(request):
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "computer.cdu.edu.cn":
            return httpx.Response(302, headers={"Location": "https://bs.cdu.edu.cn/info/1/1.htm"})
        return httpx.Response(200, text="<html>正文</html>", headers={"content-type": "text/html"})
    with Fetcher(transport=httpx.MockTransport(handler), interval=0) as fetcher:
        _, final = fetcher.fetch(URL)
    assert final == "https://bs.cdu.edu.cn/info/1/1.htm"
    assert calls[2] == "https://bs.cdu.edu.cn/robots.txt"
