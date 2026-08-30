import urllib.error
from datetime import UTC, datetime
from io import BytesIO

import pytest

from pitadvisor.ingest import http
from pitadvisor.ingest.http import USER_AGENT, FetchError, Unconditional, fetch
from pitadvisor.ingest.ratelimit import LedgerEntry, RateLimiter


class FakeResponse:
    def __init__(self, status=200, body=b"{}", headers=None):
        self.status = status
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Opener:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request)
        result = self.responses.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://x", code, "err", headers or {}, BytesIO(b""))


def test_fetch_returns_the_body(ledger):
    opener = Opener(FakeResponse(body=b'{"a":1}', headers={"ETag": '"v1"'}))
    response = fetch("https://api.jolpi.ca/x", ledger, opener=opener)
    assert response.status == 200
    assert response.body == b'{"a":1}'
    assert response.etag == '"v1"'


def test_fetch_sends_the_user_agent(ledger):
    opener = Opener(FakeResponse())
    fetch("https://api.jolpi.ca/x", ledger, opener=opener)
    assert opener.requests[0].get_header("User-agent") == USER_AGENT


def test_fetch_records_every_call_in_the_ledger(ledger):
    fetch("https://api.jolpi.ca/x", ledger, opener=Opener(FakeResponse()))
    assert ledger.recorded[0].status == 200


def test_a_known_etag_becomes_a_conditional_request(ledger):
    ledger.entries["https://api.jolpi.ca/x"] = LedgerEntry(
        url="https://api.jolpi.ca/x",
        fetched_at=datetime.now(UTC),
        status=200,
        etag='"v1"',
    )
    opener = Opener(FakeResponse())
    fetch("https://api.jolpi.ca/x", ledger, opener=opener)
    assert opener.requests[0].get_header("If-none-match") == '"v1"'


def test_a_304_is_a_result_not_an_error(ledger):
    response = fetch("https://api.jolpi.ca/x", ledger, opener=Opener(http_error(304)))
    assert response.not_modified
    assert response.body == b""


def test_a_304_still_costs_a_ledger_entry(ledger):
    fetch("https://api.jolpi.ca/x", ledger, opener=Opener(http_error(304)))
    assert [entry.status for entry in ledger.recorded] == [304]


def test_a_429_is_retried(ledger, bucket):
    opener = Opener(http_error(429), FakeResponse(body=b"{}"))
    limiter = RateLimiter(bucket, sleep=lambda _: None, jitter=lambda: 0.0)
    response = fetch("https://api.jolpi.ca/x", ledger, limiter, opener=opener)
    assert response.status == 200
    assert len(ledger.recorded) == 2


def test_a_404_raises(ledger):
    with pytest.raises(FetchError) as raised:
        fetch("https://api.jolpi.ca/x", ledger, opener=Opener(http_error(404)))
    assert raised.value.status == 404


def test_a_network_failure_raises_after_the_retries(ledger, bucket):
    opener = Opener(*[urllib.error.URLError("boom")] * 4)
    limiter = RateLimiter(bucket, sleep=lambda _: None, jitter=lambda: 0.0)
    with pytest.raises(FetchError):
        fetch("https://api.jolpi.ca/x", ledger, limiter, opener=opener)


def test_plain_http_is_refused(ledger):
    with pytest.raises(ValueError, match="non-https"):
        fetch("http://api.jolpi.ca/x", ledger, opener=Opener(FakeResponse()))


def test_the_limiter_is_asked_before_every_attempt(ledger, bucket):
    taken = []

    class Counting(RateLimiter):
        def acquire(self, tokens=1):
            taken.append(tokens)
            return super().acquire(tokens)

    limiter = Counting(bucket, sleep=lambda _: None, jitter=lambda: 0.0)
    fetch("https://api.jolpi.ca/x", ledger, limiter, opener=Opener(http_error(500), FakeResponse()))
    assert taken == [1, 1]


def test_unconditional_hides_the_stored_etag(ledger):
    ledger.entries["https://api.jolpi.ca/x"] = LedgerEntry(
        url="https://api.jolpi.ca/x",
        fetched_at=datetime.now(UTC),
        status=200,
        etag='"v1"',
    )
    opener = Opener(FakeResponse())
    fetch("https://api.jolpi.ca/x", Unconditional(ledger), opener=opener)
    assert opener.requests[0].get_header("If-none-match") is None
    assert ledger.recorded[-1].status == 200


def test_a_retry_after_header_is_what_the_wait_is_built_from(ledger):
    waits = []

    class Limiter:
        def acquire(self):
            return None

        def backoff(self, attempt, base=2.0):
            waits.append(("backoff", attempt))

        def sleep(self, seconds):
            waits.append(("retry-after", seconds))

    attempts = []

    def opener(request, timeout):
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url, 429, "slow down", {"Retry-After": "7"}, None
            )
        return FakeResponse(200, b'{"ok": true}')

    http.fetch("https://example.com/x", ledger, Limiter(), opener=opener)
    assert waits == [("retry-after", 7.0)]


def test_without_a_retry_after_the_backoff_is_the_fallback(ledger):
    waits = []

    class Limiter:
        def acquire(self):
            return None

        def backoff(self, attempt, base=2.0):
            waits.append(("backoff", attempt))

        def sleep(self, seconds):
            waits.append(("retry-after", seconds))

    attempts = []

    def opener(request, timeout):
        attempts.append(request)
        if len(attempts) == 1:
            raise urllib.error.HTTPError(request.full_url, 503, "later", {}, None)
        return FakeResponse(200, b'{"ok": true}')

    http.fetch("https://example.com/x", ledger, Limiter(), opener=opener)
    assert waits == [("backoff", 0)]


def test_an_absurd_retry_after_is_capped():
    assert http._retry_after("100000") == http.MAX_RETRY_AFTER
    assert http._retry_after("Wed, 21 Oct 2026 07:28:00 GMT") is None
    assert http._retry_after(None) is None
