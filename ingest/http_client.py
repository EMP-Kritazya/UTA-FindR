"""A polite HTTP client shared by every crawler.

What "polite" means here, and why:
- Rate limit per host (default 1 req/s). UTA's servers aren't ours; hammering
  them could hurt real students and get us blocked.
- Identifying User-Agent with a contact address, so an admin can reach us
  instead of just banning the IP.
- Retries with exponential backoff on 429/5xx/timeouts, honoring Retry-After.
  Transient failures are normal; backing off gives the server room to recover.
- A 403 means "stop". We halt that host for the rest of the run and report it.
- robots.txt is checked and disallowed URLs are flagged in the log, but not
  blocked (project decision 2026-10-15, see docs/crawler-spec.md §1).
- Conditional GET: if we already have a file, send its ETag / Last-Modified.
  The server answers 304 Not Modified with no body when nothing changed, which
  saves bandwidth for both sides and makes re-runs cheap.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Callable
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx
from tenacity import RetryCallState, Retrying, retry_if_exception_type, stop_after_attempt

from ingest.config import Settings, get_settings

log = logging.getLogger("ingest.http")

RETRY_STATUSES = {429, 500, 502, 503, 504}
MAX_RETRY_AFTER_SEC = 300.0


class HostHalted(Exception):
    """The host returned 403 (or we were otherwise told to stop); no more requests to it."""


class RetryableStatus(Exception):
    def __init__(self, status: int, url: str, retry_after: float | None):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
        self.retry_after = retry_after


@dataclass
class Validators:
    """What we remember from a previous fetch, used for conditional GET."""
    etag: str | None = None
    last_modified: str | None = None


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content: bytes
    headers: httpx.Headers
    elapsed_ms: int
    robots_allowed: bool = True

    @property
    def not_modified(self) -> bool:
        return self.status == 304

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300


def parse_retry_after(value: str | None) -> float | None:
    """Retry-After is either seconds ("120") or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


@dataclass
class PoliteClient:
    settings: Settings = field(default_factory=get_settings)
    transport: httpx.BaseTransport | None = None  # tests inject a mock transport
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    max_attempts: int = 4
    backoff_base_sec: float = 2.0

    def __post_init__(self):
        self._client = httpx.Client(
            headers={"User-Agent": self.settings.user_agent},
            timeout=self.settings.http_timeout_sec,
            follow_redirects=True,
            transport=self.transport,
        )
        self._interval = 1.0 / self.settings.rate_limit_per_sec
        self._next_slot: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self.halted_hosts: dict[str, str] = {}
        self.robots_disallowed: dict[str, int] = {}  # host -> count, for the run summary

    # ---- lifecycle -------------------------------------------------------
    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- rate limiting ---------------------------------------------------
    def _wait_turn(self, host: str):
        # Simplest token bucket: one token per host, refilled every `interval`.
        now = self.clock()
        slot = self._next_slot.get(host, now)
        if slot > now:
            self.sleep(slot - now)
        self._next_slot[host] = max(slot, now) + self._interval

    # ---- robots.txt (advisory only) ---------------------------------------
    def _robots_for(self, scheme: str, host: str) -> RobotFileParser | None:
        if host in self._robots:
            return self._robots[host]
        rp: RobotFileParser | None = None
        try:
            self._wait_turn(host)
            r = self._client.get(f"{scheme}://{host}/robots.txt")
            if r.status_code == 200 and "text" in r.headers.get("content-type", "text/plain"):
                rp = RobotFileParser()
                rp.parse(r.text.splitlines())
        except httpx.HTTPError as e:
            log.info("robots.txt fetch failed for %s: %s", host, e)
        self._robots[host] = rp
        return rp

    def _check_robots(self, url: str) -> bool:
        parts = urlsplit(url)
        rp = self._robots_for(parts.scheme, parts.netloc)
        if rp is None or rp.can_fetch(self.settings.user_agent, url):
            return True
        n = self.robots_disallowed.get(parts.netloc, 0)
        self.robots_disallowed[parts.netloc] = n + 1
        if n == 0:  # warn once per host; every request line also carries robots=disallowed
            log.warning("robots.txt disallows %s (and maybe more on this host); continuing per project policy",
                        url)
        return False

    # ---- retry policy ----------------------------------------------------
    def _wait_strategy(self, state: RetryCallState) -> float:
        exc = state.outcome.exception() if state.outcome else None
        if isinstance(exc, RetryableStatus) and exc.retry_after is not None:
            return min(exc.retry_after, MAX_RETRY_AFTER_SEC)
        # 2s, 4s, 8s ... doubling gives an overloaded server more and more room.
        return self.backoff_base_sec * 2 ** (state.attempt_number - 1)

    # ---- main API --------------------------------------------------------
    def get(self, url: str, prev: Validators | None = None,
            headers: dict[str, str] | None = None) -> FetchResult:
        host = urlsplit(url).netloc
        if host in self.halted_hosts:
            raise HostHalted(f"{host} halted earlier: {self.halted_hosts[host]}")

        robots_ok = self._check_robots(url)
        req_headers = dict(headers or {})
        if prev and prev.etag:
            req_headers["If-None-Match"] = prev.etag
        if prev and prev.last_modified:
            req_headers["If-Modified-Since"] = prev.last_modified

        retrying = Retrying(
            stop=stop_after_attempt(self.max_attempts),
            retry=retry_if_exception_type((RetryableStatus, httpx.TimeoutException, httpx.TransportError)),
            wait=self._wait_strategy,
            sleep=self.sleep,
            reraise=True,
        )
        for attempt in retrying:
            with attempt:
                return self._get_once(url, host, req_headers, robots_ok)
        raise AssertionError("unreachable")

    def _get_once(self, url: str, host: str, headers: dict[str, str], robots_ok: bool) -> FetchResult:
        self._wait_turn(host)
        t0 = time.perf_counter()
        try:
            r = self._client.get(url, headers=headers)
        except httpx.HTTPError as e:
            log.info("GET %s -> error %s", url, type(e).__name__)
            raise
        ms = int((time.perf_counter() - t0) * 1000)
        log.info("GET %s -> %s %dB %dms%s", url, r.status_code, len(r.content), ms,
                 "" if robots_ok else " robots=disallowed")

        if r.status_code == 403:
            self.halted_hosts[host] = f"HTTP 403 on {url}"
            log.error("HALTING host %s: got 403 on %s", host, url)
            raise HostHalted(self.halted_hosts[host])
        if r.status_code in RETRY_STATUSES:
            raise RetryableStatus(r.status_code, url, parse_retry_after(r.headers.get("Retry-After")))

        return FetchResult(url=url, final_url=str(r.url), status=r.status_code, content=r.content,
                           headers=r.headers, elapsed_ms=ms, robots_allowed=robots_ok)

    def get_json(self, url: str, params: dict | None = None):
        """GET a JSON API. Non-2xx raises, because an API error page is not data."""
        if params:
            url = str(httpx.URL(url, params=params))
        res = self.get(url)
        if not res.ok:
            raise HTTPError(res.status, url)
        return json.loads(res.content)


class HTTPError(Exception):
    def __init__(self, status: int, url: str):
        super().__init__(f"HTTP {status} for {url}")
        self.status = status
