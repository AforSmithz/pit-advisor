import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from pitadvisor.ingest.ratelimit import Ledger, LedgerEntry, RateLimiter

USER_AGENT = "pit-advisor/0.1 (personal project; contact via github)"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 4
TIMEOUT_SECONDS = 20.0

Opener = Callable[[urllib.request.Request, float], Any]


class Response(BaseModel, frozen=True):
    url: str
    status: int
    body: bytes
    etag: str | None = None
    last_modified: str | None = None
    fetched_at: datetime

    @property
    def not_modified(self) -> bool:
        return self.status == 304


class FetchError(RuntimeError):
    def __init__(self, url: str, status: int, detail: str = "") -> None:
        super().__init__(f"{status} on {url}{f': {detail}' if detail else ''}")
        self.url = url
        self.status = status


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def _headers(entry: LedgerEntry | None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if entry is None or entry.status not in (200, 304):
        return headers
    if entry.etag:
        headers["If-None-Match"] = entry.etag
    if entry.last_modified:
        headers["If-Modified-Since"] = entry.last_modified
    return headers


def fetch(
    url: str,
    ledger: Ledger,
    limiter: RateLimiter | None = None,
    timeout: float = TIMEOUT_SECONDS,
    opener: Opener = _open,
) -> Response:
    if not url.startswith("https://"):
        raise ValueError(f"refusing a non-https url: {url}")
    known = ledger.lookup(url)
    request = urllib.request.Request(url, headers=_headers(known))
    last: BaseException | None = None
    for attempt in range(MAX_ATTEMPTS):
        if limiter is not None:
            limiter.acquire()
        try:
            with opener(request, timeout) as raw:
                response = Response(
                    url=url,
                    status=int(raw.status),
                    body=raw.read(),
                    etag=raw.headers.get("ETag"),
                    last_modified=raw.headers.get("Last-Modified"),
                    fetched_at=datetime.now(UTC),
                )
        except urllib.error.HTTPError as exc:
            # jolpica counts a 304 against the hour budget too, so it is recorded like a hit
            response = Response(
                url=url,
                status=int(exc.code),
                body=b"",
                etag=exc.headers.get("ETag") or (known.etag if known else None),
                last_modified=exc.headers.get("Last-Modified"),
                fetched_at=datetime.now(UTC),
            )
            last = exc
        except urllib.error.URLError as exc:
            last = exc
            if attempt == MAX_ATTEMPTS - 1:
                raise FetchError(url, 0, str(exc.reason)) from exc
            if limiter is not None:
                limiter.backoff(attempt)
            continue
        ledger.record(
            LedgerEntry(
                url=url,
                fetched_at=response.fetched_at,
                status=response.status,
                etag=response.etag,
                last_modified=response.last_modified,
            )
        )
        if response.status in RETRY_STATUSES and attempt < MAX_ATTEMPTS - 1:
            if limiter is not None:
                limiter.backoff(attempt)
            continue
        if response.status >= 400 and response.status != 304:
            raise FetchError(url, response.status, str(last) if last else "")
        return response
    raise FetchError(url, 0, "retries exhausted")
