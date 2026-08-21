import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from pitadvisor.ingest.http import Response
from pitadvisor.ingest.ratelimit import LedgerEntry, LocalBucket, LocalLedger
from pitadvisor.ingest.raw_store import LocalObjectStore, RawStore

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


class RecordingLedger:
    def __init__(self):
        self.entries = {}
        self.recorded = []

    def lookup(self, url):
        return self.entries.get(url)

    def record(self, entry):
        self.entries[entry.url] = entry
        self.recorded.append(entry)


class FakeFetch:
    """Serves the jolpica and open-meteo fixtures by url, and counts what was asked for."""

    def __init__(self, status=200, bodies=None):
        self.status = status
        self.bodies = bodies or {}
        self.calls = []

    def payload_for(self, url: str) -> dict:
        for name, body in self.bodies.items():
            if name in url:
                return body
        if "open-meteo" in url or "archive-api" in url:
            return fixture("open_meteo/forecast.json")
        for resource in ("results", "qualifying", "laps", "pitstops"):
            if f"/{resource}.json" in url:
                return fixture(f"jolpica/{resource}.json")
        return fixture("jolpica/races.json")

    def __call__(self, url, ledger, limiter=None, **_):
        self.calls.append(url)
        if limiter is not None:
            limiter.acquire()
        body = b"" if self.status == 304 else json.dumps(self.payload_for(url)).encode()
        response = Response(
            url=url,
            status=self.status,
            body=body,
            etag='"abc"',
            fetched_at=datetime.now(UTC),
        )
        ledger.record(
            LedgerEntry(
                url=url,
                fetched_at=response.fetched_at,
                status=response.status,
                etag=response.etag,
            )
        )
        return response


@pytest.fixture
def store(tmp_path):
    return LocalObjectStore(tmp_path / "lake")


@pytest.fixture
def raw(store):
    return RawStore(store)


@pytest.fixture
def ledger():
    return RecordingLedger()


@pytest.fixture
def bucket(tmp_path):
    return LocalBucket(tmp_path / "quota.json")


@pytest.fixture
def local_ledger(tmp_path):
    return LocalLedger(tmp_path / "ledger.json")


@pytest.fixture
def fetch():
    return FakeFetch()


@pytest.fixture
def payload():
    return fixture


@pytest.fixture
def fetch_factory():
    return FakeFetch
