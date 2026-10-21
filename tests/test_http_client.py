"""PoliteClient tests. respx fakes the HTTP server, so no request leaves the machine.

A FakeTime object replaces time.sleep / time.monotonic: sleeps are recorded and
advance a fake clock instantly, so retry/backoff tests run in milliseconds and
we can assert exactly how long the client *would* have waited.
"""
import httpx
import pytest
import respx

from ingest.config import Settings
from ingest.http_client import HostHalted, PoliteClient, RetryableStatus, Validators

URL = "https://example.test/syllabus.pdf"
ROBOTS = "https://example.test/robots.txt"


class FakeTime:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds

    def clock(self):
        return self.now


@pytest.fixture
def ft():
    return FakeTime()


@pytest.fixture
def client(ft):
    settings = Settings(contact_email="test@example.com", rate_limit_per_sec=1.0)
    c = PoliteClient(settings=settings, sleep=ft.sleep, clock=ft.clock)
    yield c
    c.close()


@pytest.fixture
def server():
    with respx.mock(assert_all_called=False) as router:
        router.get(ROBOTS).mock(return_value=httpx.Response(404))
        yield router


def test_sends_identifying_user_agent(client, server):
    route = server.get(URL).mock(return_value=httpx.Response(200, content=b"%PDF-"))
    client.get(URL)
    ua = route.calls.last.request.headers["User-Agent"]
    assert ua.startswith("UTAFindR/") and "test@example.com" in ua


def test_conditional_get_sends_validators_and_reports_304(client, server):
    route = server.get(URL).mock(return_value=httpx.Response(304))
    res = client.get(URL, prev=Validators(etag='"abc"', last_modified="Thu, 21 Dec 2023 20:48:05 GMT"))
    sent = route.calls.last.request.headers
    assert sent["If-None-Match"] == '"abc"'
    assert sent["If-Modified-Since"] == "Thu, 21 Dec 2023 20:48:05 GMT"
    assert res.not_modified and res.content == b""


def test_no_validators_on_first_fetch(client, server):
    route = server.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
    client.get(URL)
    assert "If-None-Match" not in route.calls.last.request.headers


def test_retries_5xx_with_exponential_backoff(client, server, ft):
    server.get(URL).mock(side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, content=b"ok")])
    res = client.get(URL)
    assert res.status == 200 and res.content == b"ok"
    assert 2.0 in ft.sleeps and 4.0 in ft.sleeps  # backoff doubles: 2s, then 4s


def test_honors_retry_after(client, server, ft):
    server.get(URL).mock(side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200)])
    client.get(URL)
    assert 7.0 in ft.sleeps


def test_gives_up_after_max_attempts(client, server):
    route = server.get(URL).mock(return_value=httpx.Response(500))
    with pytest.raises(RetryableStatus):
        client.get(URL)
    assert route.call_count == client.max_attempts


def test_403_halts_the_host(client, server):
    route = server.get(URL).mock(return_value=httpx.Response(403))
    with pytest.raises(HostHalted):
        client.get(URL)
    with pytest.raises(HostHalted):
        client.get("https://example.test/another.pdf")  # same host: refused locally
    assert route.call_count == 1  # 403 is never retried, and nothing more is sent


def test_robots_disallow_warns_but_still_fetches(client, server):
    server.get(ROBOTS).mock(return_value=httpx.Response(200, text="User-agent: *\nDisallow: /",
                                                        headers={"Content-Type": "text/plain"}))
    route = server.get(URL).mock(return_value=httpx.Response(200, content=b"x"))
    res = client.get(URL)
    assert route.called  # project policy: log, don't block
    assert res.robots_allowed is False
    assert client.robots_disallowed["example.test"] == 1


def test_rate_limit_spaces_requests_per_host(client, server, ft):
    times = []

    def record(request):
        times.append(ft.now)
        return httpx.Response(200)

    server.get(URL).mock(side_effect=record)
    for _ in range(3):
        client.get(URL)
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert all(g >= 1.0 for g in gaps)  # 1 req/s per host
