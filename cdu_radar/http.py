import logging
import time
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx

from .models import RadarError, official_url, utc_now

LOG = logging.getLogger(__name__)
USER_AGENT = "cdu-opportunity-radar/0.1 (local research; low-frequency notice collector)"


class FetchError(RadarError):
    pass


class Fetcher:
    def __init__(self, *, timeout=20.0, retries=2, interval=2.0, trust_env=True,
                 transport=None, event_sink=None, sleep=time.sleep, clock=time.monotonic):
        if timeout <= 0 or retries < 0 or interval < 0:
            raise ValueError("Invalid request policy")
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
            start, requested_at = self.clock(), utc_now()
            self.last_request = start
            response, error = None, None
            try:
                response = self.client.get(url)
                status = response.status_code
                if status in {502, 503, 504}:
                    error = f"HTTP {status}"
                elif status >= 400:
                    error = f"HTTP {status}"
            except httpx.RequestError as exc:
                error = type(exc).__name__
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
                raise FetchError(f"{url}: {error} after {attempt} attempt(s)")
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
                    robot_url = official_url(urljoin(robot_url, r.headers["location"]))
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
                    raise FetchError(f"robots unavailable: HTTP {r.status_code}")
                delay = parser.crawl_delay(USER_AGENT) or 0
                rate = parser.request_rate(USER_AGENT)
                if rate and rate.requests > 0:
                    delay = max(delay, rate.seconds / rate.requests)
                self.delays[origin] = delay
                self.robots[origin] = parser
            except FetchError as exc:
                # Cache failures too, to avoid repeatedly hitting a broken origin.
                self.robots[origin] = str(exc)
        policy = self.robots[origin]
        if isinstance(policy, str):
            raise FetchError(f"{origin}: robots check failed: {policy}")
        if not policy.can_fetch(USER_AGENT, url):
            raise FetchError(f"robots disallows {url}")

    def fetch(self, url: str) -> tuple[bytes, str]:
        current = official_url(url)
        for _ in range(6):
            self._check_robots(current)
            response = self._request(current)
            if response.is_redirect:
                if not response.headers.get("location"):
                    raise FetchError(f"{current}: redirect without Location")
                current = official_url(urljoin(current, response.headers["location"]))
                continue
            if response.status_code != 200:
                # Includes 401/403/429: do not retry, solve challenges or authenticate.
                raise FetchError(f"{current}: HTTP {response.status_code}")
            if len(response.content) > 3 * 1024 * 1024:
                raise FetchError(f"{current}: response exceeds 3 MiB")
            content_type = response.headers.get("content-type", "").lower()
            if content_type and not any(t in content_type for t in ["text/html", "application/xhtml+xml"]):
                raise FetchError(f"{current}: expected HTML, got {content_type}")
            return response.content, current
        raise FetchError(f"{url}: redirect limit reached")
