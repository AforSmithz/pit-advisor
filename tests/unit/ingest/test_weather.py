from datetime import UTC, date, datetime

from pitadvisor.ingest.raw_store import write_bronze
from pitadvisor.ingest.weather import (
    WeatherClient,
    endpoint,
    event_circuits,
    ingest_event,
    is_forecast,
    parse,
)
from pitadvisor.quality.contracts import RaceRow
from pitadvisor.types import EventKey, Source

KEY = EventKey(season=2024, round=5)
STAMP = {"run_id": "run-1", "ingested_at": datetime(2024, 5, 6, tzinfo=UTC)}
RACE_DAY = date(2024, 5, 5)


def client(raw, ledger, fetch):
    return WeatherClient(raw, ledger, None, "run-1", fetch)


def test_a_race_next_week_is_a_forecast():
    assert is_forecast(date(2024, 5, 5), date(2024, 4, 30))


def test_a_race_last_year_comes_from_the_archive():
    assert not is_forecast(date(2023, 5, 5), date(2024, 4, 30))


def test_a_race_beyond_the_horizon_is_not_a_forecast_yet():
    assert not is_forecast(date(2024, 6, 30), date(2024, 4, 30))


def test_forecast_endpoint_asks_for_the_probability():
    url = endpoint(1.29, 103.86, RACE_DAY, True)
    assert url.startswith("https://api.open-meteo.com/v1/forecast")
    assert "precipitation_probability" in url


def test_archive_endpoint_drops_the_probability():
    url = endpoint(1.29, 103.86, RACE_DAY, False)
    assert url.startswith("https://archive-api.open-meteo.com/v1/archive")
    assert "precipitation_probability" not in url


def test_parse_builds_one_row_per_hour(payload):
    rows = parse(payload("open_meteo/forecast.json"), "synthetica", True, STAMP, KEY)
    assert len(rows) == 4
    assert rows[2]["precipitation_mm"] == 1.4
    assert rows[2]["observed_at"] == datetime(2024, 5, 5, 14, tzinfo=UTC)
    assert rows[0]["is_forecast"] is True


def test_parse_survives_a_missing_series(payload):
    body = payload("open_meteo/forecast.json")
    del body["hourly"]["relative_humidity_2m"]
    assert parse(body, "synthetica", True, STAMP, KEY)[0]["relative_humidity"] is None


def test_ingest_writes_bronze_and_raw(store, raw, ledger, fetch):
    outcome = ingest_event(
        client(raw, ledger, fetch), store, KEY, "synthetica", 1.29, 103.86, RACE_DAY
    )
    assert outcome.rows == 4
    assert outcome.source is Source.OPEN_METEO
    assert store.exists("bronze/table=weather/season=2024/round=05/weather.parquet")


def test_a_304_falls_back_to_the_snapshot(store, raw, ledger, fetch, fetch_factory):
    ingest_event(client(raw, ledger, fetch), store, KEY, "synthetica", 1.29, 103.86, RACE_DAY)
    stale = fetch_factory(status=304)
    outcome = ingest_event(
        client(raw, ledger, stale), store, KEY, "synthetica", 1.29, 103.86, RACE_DAY
    )
    assert outcome.not_modified
    assert outcome.rows == 4


def test_a_304_without_a_snapshot_refetches(store, raw, ledger, fetch_factory):
    class OnceStale(fetch_factory):
        def __call__(self, url, ledger, limiter=None, **kwargs):
            self.status = 200 if self.calls else 304
            return super().__call__(url, ledger, limiter, **kwargs)

    outcome = ingest_event(
        client(raw, ledger, OnceStale(status=304)), store, KEY, "synthetica", 1.29, 103.86, RACE_DAY
    )
    assert outcome.rows == 4
    assert not outcome.not_modified


def test_event_circuits_reads_the_bronze_schedule(store):
    rows = [
        RaceRow(
            run_id="run-1",
            ingested_at=datetime(2024, 5, 6, tzinfo=UTC),
            season=season,
            round=round_,
            race_name="Synthetic",
            circuit_id=f"c{round_}",
            circuit_name="Ring",
            latitude=1.0,
            longitude=2.0,
            race_date=RACE_DAY,
        )
        for season, round_ in ((2024, 2), (2024, 1), (2023, 1))
    ]
    write_bronze(store, "races", EventKey(season=2024, round=1), rows)
    found = event_circuits(store, 2024)
    assert [circuit.round for circuit in found] == [1, 2]


def test_event_circuits_is_empty_without_bronze(store):
    assert event_circuits(store, 2024) == []
