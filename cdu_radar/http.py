import logging
import ipaddress
import socket
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .models import RadarError, official_url, utc_now

LOG = logging.getLogger(__name__)
USER_AGENT = "cdu-opportunity-radar/0.1 (local research; low-frequency notice collector)"


class ResolveTransport(httpx.BaseTransport):
    """Explicit DNS override, retaining the original Host, SNI and certificate check.

    No OS DNS/network setting is changed. Only configured official hosts and
    global IP addresses may be provided. This is opt-in, never a TLS downgrade.
    """
    def __init__(self, mapping, allowed_hosts):
        self.mapping = {}
        for host, value in mapping.items():
            if host not in allowed_hosts or not ipaddress.ip_address(value).is_global:
                raise ValueError("DNS overrides require a configured host and a global IP")
            self.mapping[host] = value
        self.inner = httpx.HTTPTransport()

    def handle_request(self, request):
        host = request.url.host
        if host in self.mapping:
            headers = request.headers.copy()
            headers["Host"] = request.url.netloc.decode("ascii")
            request = httpx.Request(request.method, request.url.copy_with(host=self.mapping[host]),
                                    headers=headers, stream=request.stream,
                                    extensions={**request.extensions, "sni_hostname": host})
        return self.inner.handle_request(request)

    def close(self):
        self.inner.close()


class FetchError(RadarError):
    def __init__(self, message, category="network", scope="environment", retryable=True):
        super().__init__(message)
        self.category, self.scope, self.retryable = category, scope, retryable


class RequestBudgetExceeded(FetchError):
    def __init__(self):
        super().__init__('Collection request budget exhausted or stopped', 'budget', 'local', True)


def classify_exception(exc):
    """Keep the distinction between DNS, TLS, proxy and temporary request errors."""
    chain = []
    current = exc
    while current is not None and current not in chain:
        chain.append(current)
        current = current.__cause__ or current.__context__
    text = " ".join(str(item) for item in chain).lower()
    if isinstance(exc, httpx.ProxyError):
        return "proxy", "environment"
    if any(isinstance(item, socket.gaierror) for item in chain) or "getaddrinfo" in text:
        return "dns", "environment"
    if "certificate_verify_failed" in text or "certificate verify failed" in text:
        return "tls_certificate", "environment"
    if "ssl" in text or "tls" in text:
        return "tls_handshake", "environment"
    if isinstance(exc, httpx.TimeoutException):
        return type(exc).__name__.lower(), "request"
    if isinstance(exc, httpx.RemoteProtocolError):
        return "protocol", "request"
    if isinstance(exc, httpx.DecodingError):
        return "response_decode", "request"
    return "connection", "environment"


class Fetcher:
    def __init__(self, *, timeout=20.0, retries=2, interval=2.0, trust_env=True,
                 transport=None, event_sink=None, sleep=time.sleep, clock=time.monotonic,
                 allowed_hosts=None, resolve=None, request_limit=None, stop_event=None):
        if timeout <= 0 or retries < 0 or interval < 0:
            raise ValueError("Invalid request policy")
        if request_limit is not None and request_limit <= 0:
            raise ValueError('request_limit must be positive')
        if allowed_hosts is None:
            from .sources import trusted_hosts
            allowed_hosts = trusted_hosts()
        self.allowed_hosts = set(allowed_hosts)
        if resolve:
            if trust_env or transport is not None:
                raise ValueError("DNS overrides require trust_env=False and no custom transport")
            transport = ResolveTransport(resolve, self.allowed_hosts)
        self.client = httpx.Client(timeout=timeout, follow_redirects=False, trust_env=trust_env,
                                   headers={"User-Agent": USER_AGENT}, transport=transport)
        self.retries = retries
        self.interval = interval
        self.sleep, self.clock = sleep, clock
        self.last_request = None
        self.robots = {}
        self.delays = {}
        self.event_sink = event_sink
        self.source_id = None
        self.request_limit, self.request_count, self.stop_event = request_limit, 0, stop_event

    def _trusted_url(self, url):
        normalized = official_url(url)
        if urlsplit(normalized).hostname not in self.allowed_hosts:
            raise FetchError(f"Unconfigured official host: {urlsplit(normalized).hostname}", "untrusted_host", "configuration", False)
        return normalized

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.client.close()

    def _request(self, url: str) -> httpx.Response:
        origin = self._origin(url)
        for attempt in range(1, self.retries + 2):
            if self.last_request is not None:
                pause = max(self.interval, self.delays.get(origin, 0)) - (self.clock() - self.last_request)
                if pause > 0:
                    self.sleep(pause)
            if ((self.stop_event is not None and self.stop_event.is_set()) or
                    self.request_limit is not None and self.request_count >= self.request_limit):
                raise RequestBudgetExceeded()
            self.request_count += 1
            start, requested_at = self.clock(), utc_now()
            self.last_request = start
            response, error = None, None
            category, scope = "connection", "environment"
            try:
                # Enforce a memory bound while streaming instead of after full download.
                with self.client.stream("GET", url) as streamed:
                    content = bytearray()
                    for chunk in streamed.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 3 * 1024 * 1024:
                            raise FetchError(f"{url}: response exceeds 3 MiB", "response_size", "request", False)
                    headers = streamed.headers.copy()
                    # iter_bytes already decompresses the response; do not decode twice.
                    headers.pop("content-encoding", None)
                    headers.pop("content-length", None)
                    response = httpx.Response(streamed.status_code, headers=headers,
                                              content=bytes(content), request=streamed.request)
                status = response.status_code
                if status in {502, 503, 504}:
                    error = f"HTTP {status}"
                elif status >= 400:
                    error = f"HTTP {status}"
            except FetchError as exc:
                elapsed = round((self.clock() - start) * 1000)
                if self.event_sink:
                    self.event_sink(self.source_id,url,requested_at,attempt,streamed.status_code,exc.category,elapsed)
                LOG.error("request url=%s attempt=%s status=%s error=%s ms=%s",url,attempt,streamed.status_code,exc.category,elapsed)
                raise
            except httpx.RequestError as exc:
                error = type(exc).__name__
                category, scope = classify_exception(exc)
            elapsed = round((self.clock() - start) * 1000)
            status = response.status_code if response is not None else None
            if self.event_sink:
                self.event_sink(self.source_id, url, requested_at, attempt, status, error, elapsed)
            LOG.info("request url=%s attempt=%s status=%s error=%s ms=%s", url, attempt, status, error, elapsed)
            retryable = response is None or status in {502, 503, 504}
            if retryable and attempt <= self.retries:
                self.sleep(min(2 ** attempt, 30))
                continue
            if response is None:
                raise FetchError(f"{url}: {error} after {attempt} attempt(s) [{category}]", category, scope)
            return response
        raise AssertionError("unreachable")

    @staticmethod
    def _origin(url):
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _check_robots(self, url):
        origin = self._origin(url)
        if origin not in self.robots:
            robot_url = origin + "/robots.txt"
            try:
                r = self._request(robot_url)
                # Fetch redirects explicitly, without allowing off-site targets.
                for _ in range(5):
                    if not r.is_redirect:
                        break
                    if not r.headers.get("location"):
                        raise FetchError("robots redirect has no Location")
                    robot_url = self._trusted_url(urljoin(robot_url, r.headers["location"]))
                    r = self._request(robot_url)
                if r.is_redirect:
                    raise FetchError("robots redirect limit reached")
                parser = RobotFileParser()
                if r.status_code in {404, 410}:
                    parser.parse([])
                elif r.status_code == 200:
                    lines = r.text.splitlines()
                    parser.parse(lines)
                    if not any(line.lstrip().lower().startswith("user-agent:") for line in lines):
                        LOG.warning("robots_no_rules url=%s status=200 (may be HTML maintenance page)", robot_url)
                else:
                    scope = "permanent" if r.status_code == 410 else "request"
                    raise FetchError(f"robots unavailable: HTTP {r.status_code}", f"http_{r.status_code}", scope, r.status_code >= 500)
                delay = parser.crawl_delay(USER_AGENT) or 0
                rate = parser.request_rate(USER_AGENT)
                if rate and rate.requests > 0:
                    delay = max(delay, rate.seconds / rate.requests)
                self.delays[origin] = delay
                self.robots[origin] = parser
            except RequestBudgetExceeded:
                raise
            except FetchError as exc:
                # Cache failures too, to avoid repeatedly hitting a broken origin.
                self.robots[origin] = exc
        policy = self.robots[origin]
        if isinstance(policy, FetchError):
            raise FetchError(f"{origin}: robots check failed: {policy}", policy.category, policy.scope, policy.retryable)
        if not policy.can_fetch(USER_AGENT, url):
            raise FetchError(f"robots disallows {url}", "robots_denied", "access_policy", False)

    def fetch(self, url: str) -> tuple[bytes, str]:
        current = self._trusted_url(url)
        for _ in range(6):
            self._check_robots(current)
            response = self._request(current)
            if response.is_redirect:
                if not response.headers.get("location"):
                    raise FetchError(f"{current}: redirect without Location")
                current = self._trusted_url(urljoin(current, response.headers["location"]))
                continue
            if response.status_code != 200:
                # Includes 401/403/429: do not retry, solve challenges or authenticate.
                code = response.status_code
                raise FetchError(f"{current}: HTTP {code}", f"http_{code}",
                                 "permanent" if code == 410 else "access_policy" if code in {401,403} else "request", code == 429 or code >= 500)
            if len(response.content) > 3 * 1024 * 1024:
                raise FetchError(f"{current}: response exceeds 3 MiB")
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in ["text/html", "application/xhtml+xml"]):
                raise FetchError(f"{current}: expected HTML, got {content_type}")
            return response.content, current
        raise FetchError(f"{url}: redirect limit reached")
