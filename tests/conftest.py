import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from pitadvisor.ingest.http import Response
from pitadvisor.ingest.ratelimit import LedgerEntry, LocalBucket, LocalLedger
from pitadvisor.ingest.raw_store import LocalObjectStore, RawStore, write_bronze
from pitadvisor.quality.contracts import (
    QualifyingRow,
    RaceRow,
    ResultRow,
    SessionLapRow,
    WeatherRow,
)
from pitadvisor.types import EventKey, SessionKey, SessionKind

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


NOW = datetime(2025, 1, 1, tzinfo=UTC)
CIRCUITS = ("bahrain", "monza", "monaco", "silverstone", "spa", "suzuka")
SEATS = {
    "alpha": ("AAA", "AAB"),
    "bravo": ("BBA", "BBB"),
    "charlie": ("CCA", "CCB"),
}
CODES = [code for pair in SEATS.values() for code in pair]
SEASONS = (2023, 2024)
LAPS = 26


def held(season: int, round_: int) -> date:
    return date(season, 3, 1) + timedelta(days=14 * (round_ - 1))


def start(season: int, round_: int) -> datetime:
    return datetime.combine(held(season, round_), datetime.min.time(), tzinfo=UTC).replace(hour=13)


def stamp() -> dict[str, object]:
    return {"run_id": "run-1", "ingested_at": NOW}


def seed(store, wet_rounds: tuple[int, ...] = (), seasons: tuple[int, ...] = SEASONS) -> None:
    for season in seasons:
        for round_, circuit in enumerate(CIRCUITS, start=1):
            key = EventKey(season=season, round=round_)
            write_bronze(store, "races", key, [race_row(season, round_, circuit)])
            write_bronze(store, "results", key, results_rows(season, round_))
            write_bronze(store, "qualifying", key, quali_rows(season, round_))
            write_bronze(store, "weather", key, weather_rows(season, round_, circuit))
            write_bronze(
                store,
                "session_laps",
                SessionKey(season=season, round=round_, session=SessionKind.RACE),
                lap_rows(season, round_, wet=round_ in wet_rounds),
            )


def race_row(season: int, round_: int, circuit: str) -> RaceRow:
    return RaceRow(
        **stamp(),
        season=season,
        round=round_,
        race_name=f"{circuit} grand prix",
        circuit_id=circuit,
        circuit_name=circuit,
        latitude=45.6,
        longitude=9.3,
        race_date=held(season, round_),
        start_utc=start(season, round_),
    )


def results_rows(season: int, round_: int) -> list[ResultRow]:
    rows = []
    for position, code in enumerate(CODES, start=1):
        team = next(name for name, pair in SEATS.items() if code in pair)
        retired = position == 6 and round_ == 2
        rows.append(
            ResultRow(
                **stamp(),
                season=season,
                round=round_,
                driver_id=code.lower(),
                driver_code=code,
                constructor_id=team,
                grid=position,
                position=None if retired else position,
                position_text="R" if retired else str(position),
                points=0.0,
                laps_completed=12 if retired else LAPS,
                status="Gearbox" if retired else "Finished",
            )
        )
    return rows


def quali_rows(season: int, round_: int) -> list[QualifyingRow]:
    rows = []
    for position, code in enumerate(CODES, start=1):
        team = next(name for name, pair in SEATS.items() if code in pair)
        base = 90_000 + position * 400
        rows.append(
            QualifyingRow(
                **stamp(),
                season=season,
                round=round_,
                driver_id=code.lower(),
                driver_code=code,
                constructor_id=team,
                position=position,
                q1_millis=base + 900,
                q2_millis=base + 400 if position <= 4 else None,
                q3_millis=base if position <= 2 else None,
            )
        )
    return rows


def weather_rows(season: int, round_: int, circuit: str) -> list[WeatherRow]:
    return [
        WeatherRow(
            **stamp(),
            season=season,
            round=round_,
            circuit_id=circuit,
            observed_at=start(season, round_) + timedelta(hours=hour),
            is_forecast=True,
            temperature_c=24.0,
            precipitation_mm=0.0,
            precipitation_probability=3.0,
            wind_speed_kph=9.0,
            relative_humidity=50.0,
        )
        for hour in range(3)
    ]


def lap_rows(season: int, round_: int, wet: bool) -> list[SessionLapRow]:
    rows = []
    for position, code in enumerate(CODES, start=1):
        # 800 ms a lap between cars, so the gap clears the traffic threshold by lap three
        pace = 90_000 + position * 800
        # staggered stops, without which lap_in_stint and laps_remaining are the same column
        # and the fit is not identifiable
        stop = 9 + position
        for lap in range(1, LAPS + 1):
            in_stint = lap if lap <= stop else lap - stop
            rows.append(
                SessionLapRow(
                    **stamp(),
                    season=season,
                    round=round_,
                    session=SessionKind.RACE,
                    driver_code=code,
                    driver_number=position,
                    lap=lap,
                    lap_time_millis=pace + 40 * in_stint,
                    stint=1 if lap <= stop else 2,
                    lap_in_stint=in_stint,
                    compound="INTERMEDIATE" if wet else "MEDIUM",
                    tyre_life=in_stint,
                    is_accurate=True,
                    track_status="1",
                    pit_in=lap == stop,
                    pit_out=lap == stop + 1,
                    position=position,
                )
            )
    return rows


@dataclass(frozen=True)
class Lake:
    """A whole synthetic bronze layer: two seasons of six real circuits, three teams of two."""

    store: LocalObjectStore
    seasons: tuple[int, ...] = SEASONS
    circuits: tuple[str, ...] = CIRCUITS
    codes: tuple[str, ...] = tuple(CODES)
    teams: tuple[str, ...] = tuple(SEATS)
    laps: int = LAPS

    @property
    def events(self) -> int:
        return len(self.seasons) * len(self.circuits)

    def held(self, season: int, round_: int) -> date:
        return held(season, round_)


@pytest.fixture(scope="session")
def seed_lake():
    def build(store, wet_rounds=(), seasons=SEASONS):
        seed(store, wet_rounds=wet_rounds, seasons=seasons)
        return Lake(store=store, seasons=seasons)

    return build


@pytest.fixture
def seeded(store, seed_lake):
    def build(wet_rounds=(), seasons=SEASONS):
        return seed_lake(store, wet_rounds=wet_rounds, seasons=seasons)

    return build
