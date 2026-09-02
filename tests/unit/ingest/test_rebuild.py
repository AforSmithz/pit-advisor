import json
from datetime import UTC, datetime

import polars as pl
import pytest

from pitadvisor.ingest import jolpica
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.ingest.rebuild import (
    latest_objects,
    parse_key,
    rebuild_bronze,
)
from pitadvisor.types import EventKey, Provenance, SessionKey, SessionKind, Source

KEY = EventKey(season=2024, round=5)
SESSION = SessionKey(season=2024, round=5, session=SessionKind.RACE)


def land(raw, key, name, body, source=Source.OPEN_METEO, fetched_at=None):
    return raw.land(
        key,
        name,
        json.dumps(body).encode(),
        Provenance(
            run_id="run-1",
            source=source,
            url=f"https://example.test/{name}",
            fetched_at=fetched_at or datetime(2024, 5, 5, 12, tzinfo=UTC),
            status=200,
        ),
    )


def ingested(store, ledger, fetch, resources=("races", "results")):
    client = jolpica.JolpicaClient(RawStore(store), ledger, run_id="run-1", fetch=fetch)
    return jolpica.ingest_event(client, store, KEY, resources)


def bronze_frames(store):
    return {
        item.key: pl.read_parquet(store.get(item.key))
        for item in store.list("bronze/")
        if item.key.endswith(".parquet")
    }


def test_parse_key_reads_an_event_payload():
    found = parse_key(
        "raw/source=jolpica/season=2024/round=05/results-offset0000-20240505T120000000Z.json"
    )
    assert found is not None
    assert found.source is Source.JOLPICA
    assert (found.season, found.round) == (2024, 5)
    assert found.session is None
    assert found.name == "results-offset0000"
    assert found.stamp == "20240505T120000000"


def test_parse_key_reads_a_session_payload():
    found = parse_key(
        "raw/source=fastf1/season=2024/round=05/session=race/session_laps-20240505T120000000Z.json"
    )
    assert found is not None
    assert found.session is SessionKind.RACE
    assert found.name == "session_laps"


@pytest.mark.parametrize(
    "key",
    [
        "raw/source=jolpica/season=2024/round=05/results-offset0000-20240505T120000000Z.json.meta.json",
        "raw/source=nowhere/season=2024/round=05/results-offset0000-20240505T120000000Z.json",
        "raw/source=jolpica/season=2024/round=05/results.json",
        "bronze/table=results/season=2024/round=05/results.parquet",
        "raw/source=jolpica/season=2024/results-offset0000-20240505T120000000Z.json",
        "raw/source=fastf1/season=2024/round=05/session=teatime/session_laps-20240505T120000000Z.json",
    ],
)
def test_parse_key_ignores_sidecars_and_strays(key):
    assert parse_key(key) is None


def test_only_the_newest_stamp_of_a_name_replays(store, raw):
    land(
        raw, KEY, "weather-archive", {"hourly": {}}, fetched_at=datetime(2024, 5, 5, 12, tzinfo=UTC)
    )
    land(
        raw, KEY, "weather-archive", {"hourly": {}}, fetched_at=datetime(2024, 5, 6, 12, tzinfo=UTC)
    )
    found = latest_objects(store)
    assert len(found) == 1
    assert found[0].stamp.startswith("20240506")


def test_rebuild_reproduces_bronze_without_fetching(store, ledger, fetch):
    ingested(store, ledger, fetch)
    before = bronze_frames(store)
    store.clear("bronze")
    assert not bronze_frames(store)
    fetched = len(fetch.calls)

    outcomes = rebuild_bronze(store, "replay-1")

    assert sum(outcome.requests for outcome in outcomes) == 0
    assert len(fetch.calls) == fetched
    after = bronze_frames(store)
    assert set(after) == set(before)
    for key, frame in after.items():
        volatile = ["run_id", "ingested_at"]
        assert frame.drop(volatile).equals(before[key].drop(volatile))


def test_rebuild_stamps_a_new_run_so_silver_prefers_it(store, ledger, fetch):
    ingested(store, ledger, fetch, resources=("races",))
    store.clear("bronze")
    rebuild_bronze(store, "replay-1")
    frame = bronze_frames(store)["bronze/table=races/season=2024/round=05/races.parquet"]
    assert frame["run_id"].to_list() == ["replay-1"]


def test_a_forecast_stays_a_forecast_however_late_it_is_replayed(store, ledger, fetch, payload):
    ingested(store, ledger, fetch, resources=("races",))
    land(RawStore(store), KEY, "weather-forecast", payload("open_meteo/forecast.json"))

    rebuild_bronze(store, "replay-1")

    frame = bronze_frames(store)["bronze/table=weather/season=2024/round=05/weather.parquet"]
    assert frame["is_forecast"].to_list() == [True] * frame.height
    assert frame["circuit_id"].unique().to_list() == ["synthetica"]


def test_an_archive_read_stays_an_archive_read(store, ledger, fetch, payload):
    ingested(store, ledger, fetch, resources=("races",))
    land(RawStore(store), KEY, "weather-archive", payload("open_meteo/forecast.json"))

    rebuild_bronze(store, "replay-1")

    frame = bronze_frames(store)["bronze/table=weather/season=2024/round=05/weather.parquet"]
    assert frame["is_forecast"].to_list() == [False] * frame.height


def test_weather_without_races_is_skipped_not_guessed(store, raw, payload):
    land(raw, KEY, "weather-archive", payload("open_meteo/forecast.json"))
    outcomes = rebuild_bronze(store, "replay-1")
    assert [outcome.skipped for outcome in outcomes] == ["no circuit in bronze races"]
    assert not bronze_frames(store)


def test_session_laps_replay_from_the_raw_copy(store, raw):
    lap = {
        "Driver": "VER",
        "DriverNumber": "1",
        "LapNumber": 1.0,
        "LapTime": 93400,
        "Stint": 1.0,
        "Compound": "MEDIUM",
        "TyreLife": 3.0,
        "TrackStatus": "1",
        "Position": 1.0,
    }
    land(raw, SESSION, "session_laps", [lap, {**lap, "LapNumber": 2.0}], source=Source.FASTF1)

    outcomes = rebuild_bronze(store, "replay-1")

    assert [(outcome.table, outcome.rows) for outcome in outcomes] == [("session_laps", 2)]
    frame = bronze_frames(store)[
        "bronze/table=session_laps/season=2024/round=05/session=race/session_laps.parquet"
    ]
    assert frame["lap_in_stint"].to_list() == [1, 2]


def test_rebuild_can_be_scoped_to_one_season(store, ledger, fetch):
    ingested(store, ledger, fetch, resources=("races",))
    assert rebuild_bronze(store, "replay-1", season=2023) == []
    assert rebuild_bronze(store, "replay-1", season=2024)


def test_rebuild_can_be_scoped_to_one_source(store, ledger, fetch, payload):
    ingested(store, ledger, fetch, resources=("races",))
    land(RawStore(store), KEY, "weather-archive", payload("open_meteo/forecast.json"))
    outcomes = rebuild_bronze(store, "replay-1", source=Source.OPEN_METEO)
    assert {outcome.table for outcome in outcomes} == {"weather"}


FIELD_BLOCK = (
    "Document 41\nDate 14 May 2024\nTime 18:22\n"
    "No / Driver 7 - Jo Mercier\nCompetitor Cobalt Racing\nSession Race\n"
    "Fact Causing a collision.\n"
    "Infringement Breach of Article 33.4 of the FIA Formula One Sporting Regulations.\n"
    "Decision 10 second time penalty\nReason The driver was wholly at fault.\n"
)
PROSE_DOC = "Document 55\nDate 14 May 2024\nTime 19:02\nThe Stewards grant permission to start.\n"


def land_document(raw, name, suffix="pdf"):
    return raw.land(
        KEY,
        name,
        b"%PDF-1.4",
        Provenance(
            run_id="run-1",
            source=Source.FIA_DOCS,
            url=f"https://www.fia.com/{name}",
            fetched_at=datetime(2024, 5, 5, 12, tzinfo=UTC),
            status=200,
        ),
        suffix=suffix,
    )


def test_a_document_with_a_field_block_needs_no_model(store, monkeypatch):
    from pitadvisor.ingest import rebuild as rebuild_module

    raw = RawStore(store)
    land_document(raw, "decision-car-7-collision")
    monkeypatch.setattr(rebuild_module, "pdf_text", lambda body: FIELD_BLOCK)
    outcomes = rebuild_module.rebuild_incidents(
        store, "run-2", latest_objects(store), {"run_id": "run-2", "ingested_at": datetime.now(UTC)}
    )
    assert [o.rows for o in outcomes] == [1]
    frame = bronze_frames(store)["bronze/table=incidents/season=2024/round=05/incidents.parquet"]
    assert frame["read_by"].to_list() == ["parsed"]
    assert frame["car"].to_list() == [7]
    assert frame["kind"].to_list() == ["decision"]


def test_prose_with_nothing_cached_is_reported_rather_than_paid_for(store, monkeypatch):
    from pitadvisor.ingest import rebuild as rebuild_module

    raw = RawStore(store)
    land_document(raw, "decision-car-22-permission")
    monkeypatch.setattr(rebuild_module, "pdf_text", lambda body: PROSE_DOC)
    outcomes = rebuild_module.rebuild_incidents(
        store, "run-2", latest_objects(store), {"run_id": "run-2", "ingested_at": datetime.now(UTC)}
    )
    assert outcomes[0].rows == 0
    assert outcomes[0].skipped == "1 documents need extracting first"
    assert "bronze/table=incidents/season=2024/round=05/incidents.parquet" not in bronze_frames(
        store
    )


def test_prose_is_read_from_the_cache_and_never_refetched(store, monkeypatch):
    from pitadvisor.incidents import lake
    from pitadvisor.incidents.parse import Decision
    from pitadvisor.ingest import rebuild as rebuild_module

    raw = RawStore(store)
    land_document(raw, "decision-car-22-permission")
    key = latest_objects(store)[0].key
    monkeypatch.setattr(rebuild_module, "pdf_text", lambda body: PROSE_DOC)
    store.put(
        lake.cache_key(key),
        lake.dump(
            lake.Reading(
                raw_key=key,
                kind="decision",
                read_by=lake.EXTRACTED,
                decisions=[Decision(document=55, car=22, driver="Kit Rasmussen")],
            )
        ),
    )
    outcomes = rebuild_module.rebuild_incidents(
        store, "run-2", latest_objects(store), {"run_id": "run-2", "ingested_at": datetime.now(UTC)}
    )
    assert outcomes[0].rows == 1
    frame = bronze_frames(store)["bronze/table=incidents/season=2024/round=05/incidents.parquet"]
    assert frame["read_by"].to_list() == ["extracted"]
    assert frame["driver"].to_list() == ["Kit Rasmussen"]


def test_a_classification_document_is_not_an_incident(store, monkeypatch):
    from pitadvisor.ingest import rebuild as rebuild_module

    raw = RawStore(store)
    land_document(raw, "final-race-classification")
    monkeypatch.setattr(rebuild_module, "pdf_text", lambda body: FIELD_BLOCK)
    outcomes = rebuild_module.rebuild_incidents(
        store, "run-2", latest_objects(store), {"run_id": "run-2", "ingested_at": datetime.now(UTC)}
    )
    assert outcomes == []
