import json
from datetime import UTC, date, datetime, timedelta

import polars as pl
import pytest

from pitadvisor.ingest.fastf1_session import (
    UnknownFormatError,
    backfill,
    held_events,
    ingest_session,
    millis,
    serialize,
    sessions_for,
    to_records,
)
from pitadvisor.types import Provenance, SessionKey, SessionKind, Source

KEY = SessionKey(season=2024, round=5, session=SessionKind.RACE)
RAW_PREFIX = "raw/source=fastf1/season=2024/round=05/session=race/"


class Laps:
    def __init__(self, rows):
        self.rows = rows

    def to_dict(self, _):
        return self.rows


def lap(**overrides):
    row = {
        "Driver": "VER",
        "DriverNumber": "1",
        "LapNumber": 1.0,
        "LapTime": timedelta(seconds=93.4),
        "Sector1Time": timedelta(seconds=30.0),
        "Sector2Time": timedelta(seconds=31.0),
        "Sector3Time": timedelta(seconds=32.4),
        "Stint": 1.0,
        "Compound": "MEDIUM",
        "TyreLife": 3.0,
        "IsPersonalBest": False,
        "Deleted": False,
        "IsAccurate": True,
        "TrackStatus": "1",
        "PitInTime": None,
        "PitOutTime": None,
        "Position": 1.0,
    }
    row.update(overrides)
    return row


def stamped():
    return {"run_id": "run-1", "ingested_at": datetime(2024, 5, 5, tzinfo=UTC)}


def records(*rows):
    return to_records(serialize(Laps(list(rows))), KEY, stamped())


def test_a_conventional_weekend_has_three_practices():
    assert sessions_for("conventional").count(SessionKind.FP1) == 1
    assert SessionKind.FP3 in sessions_for("conventional")


def test_a_sprint_weekend_has_one_practice():
    kinds = sessions_for("sprint")
    assert SessionKind.FP3 not in kinds
    assert SessionKind.SPRINT in kinds


def test_2023_ran_the_shootout_and_2024_ran_sprint_quali():
    assert SessionKind.SPRINT_SHOOTOUT in sessions_for("sprint_shootout")
    assert SessionKind.SPRINT_QUALIFYING in sessions_for("sprint_qualifying")


def test_an_unknown_format_is_loud():
    with pytest.raises(UnknownFormatError):
        sessions_for("grand_prix_of_atlantis")


def test_millis_rounds_a_timedelta():
    assert millis(timedelta(seconds=93.4)) == 93400


def test_millis_of_a_missing_time_is_none():
    assert millis(None) is None
    assert millis(float("nan")) is None
    assert millis(timedelta(0)) is None


def test_lap_in_stint_counts_within_the_stint():
    rows = records(*[lap(LapNumber=n, Stint=1.0 if n < 3 else 2.0) for n in (1, 2, 3, 4)])
    assert [row["lap_in_stint"] for row in rows] == [1, 2, 1, 2]


def test_lap_in_stint_is_per_driver():
    rows = records(lap(Driver="VER"), lap(Driver="NOR"), lap(Driver="VER", LapNumber=2.0))
    assert [row["lap_in_stint"] for row in rows] == [1, 1, 2]


def test_deleted_and_pit_flags_survive():
    rows = records(lap(Deleted=True, PitInTime=timedelta(seconds=1)))
    assert rows[0]["is_deleted"] is True
    assert rows[0]["pit_in"] is True
    assert rows[0]["pit_out"] is False


def test_track_status_stays_a_string():
    rows = records(lap(TrackStatus="24"))
    assert rows[0]["track_status"] == "24"


def test_serialize_turns_times_into_millis_and_keeps_every_column():
    row = serialize(Laps([lap(FreshTyre=True)]))[0]
    assert row["LapTime"] == 93400
    assert row["FreshTyre"] is True
    assert row["PitInTime"] is None
    assert json.dumps(row)


def test_serialize_keeps_a_timestamp_as_an_iso_string():
    row = serialize(Laps([lap(LapStartDate=datetime(2024, 5, 5, 13, 4, tzinfo=UTC))]))[0]
    assert row["LapStartDate"].startswith("2024-05-05T13:04")


def test_ingest_session_writes_bronze(store):
    outcome = ingest_session(
        store,
        KEY,
        cache_dir=None,
        run_id="run-1",
        loader=lambda *_: Laps([lap(), lap(LapNumber=2.0)]),
    )
    assert outcome.rows == 2
    assert outcome.source is Source.FASTF1
    frame = pl.read_parquet(
        store.get(
            "bronze/table=session_laps/season=2024/round=05/session=race/session_laps.parquet"
        )
    )
    assert frame["session"][0] == "race"


def test_a_lap_without_a_number_is_quarantined(store):
    outcome = ingest_session(
        store, KEY, cache_dir=None, run_id="run-1", loader=lambda *_: Laps([lap(LapNumber=None)])
    )
    assert outcome.rows == 0
    assert outcome.quarantined == 1


def test_ingest_session_lands_raw_before_bronze(store):
    ingest_session(
        store, KEY, cache_dir=None, run_id="run-1", loader=lambda *_: Laps([lap(), lap()])
    )
    landed = [item.key for item in store.list(RAW_PREFIX)]
    payload = [key for key in landed if key.endswith(".json") and ".meta" not in key]
    assert len(payload) == 1
    assert len(json.loads(store.get(payload[0]))) == 2
    meta = Provenance.model_validate_json(store.get(payload[0] + ".meta.json"))
    assert meta.source is Source.FASTF1
    assert meta.url == "fastf1://2024/05/race"


def test_bronze_is_rebuildable_from_the_raw_copy(store):
    ingest_session(
        store, KEY, cache_dir=None, run_id="run-1", loader=lambda *_: Laps([lap(), lap()])
    )
    raw_key = next(
        item.key
        for item in store.list(RAW_PREFIX)
        if item.key.endswith(".json") and ".meta" not in item.key
    )
    rebuilt = to_records(json.loads(store.get(raw_key)), KEY, stamped())
    bronze = pl.read_parquet(
        store.get(
            "bronze/table=session_laps/season=2024/round=05/session=race/session_laps.parquet"
        )
    )
    assert [row["lap_time_millis"] for row in rebuilt] == list(bronze["lap_time_millis"])


def test_the_cache_round_trips_through_the_lake(store, tmp_path):
    from pitadvisor.ingest.fastf1_session import pull_cache, push_cache

    warm = tmp_path / "warm"
    (warm / "2024" / "race").mkdir(parents=True)
    (warm / "2024" / "race" / "laps.ff1pkl").write_bytes(b"cached")
    assert push_cache(store, warm) == 1
    assert push_cache(store, warm) == 0  # nothing changed, nothing re-uploaded

    cold = tmp_path / "cold"
    assert pull_cache(store, cold) == 1
    assert (cold / "2024" / "race" / "laps.ff1pkl").read_bytes() == b"cached"
    assert pull_cache(store, cold) == 0


def test_a_session_ingest_can_skip_the_cache_entirely(store, tmp_path):
    ingest_session(
        store, KEY, cache_dir=tmp_path / "cache", run_id="run-1", loader=lambda *_: Laps([lap()])
    )
    assert not list(store.list("cache/"))


def test_a_synced_ingest_leaves_the_cache_in_the_lake(store, tmp_path):
    cache = tmp_path / "cache"

    def loader(*_):
        (cache / "2024").mkdir(parents=True, exist_ok=True)
        (cache / "2024" / "session.ff1pkl").write_bytes(b"warm")
        return Laps([lap()])

    ingest_session(store, KEY, cache_dir=cache, run_id="run-1", loader=loader, sync_cache=True)
    assert [item.key for item in store.list("cache/")] == ["cache/fastf1/2024/session.ff1pkl"]


def schedule(**formats):
    return lambda season, cache_dir: {int(r): f for r, f in formats.items()}


def test_backfill_pulls_the_race_of_every_round(store):
    outcomes = backfill(
        store,
        2024,
        cache_dir=None,
        run_id="run-1",
        loader=lambda *_: Laps([lap()]),
        schedule=schedule(**{"1": "conventional", "2": "sprint_qualifying"}),
    )
    assert [o.round for o in outcomes] == [1, 2]
    assert store.exists(
        "bronze/table=session_laps/season=2024/round=02/session=race/session_laps.parquet"
    )


def test_backfill_asks_the_format_which_sessions_exist(store):
    outcomes = backfill(
        store,
        2024,
        cache_dir=None,
        run_id="run-1",
        kinds=(SessionKind.FP2, SessionKind.SPRINT),
        loader=lambda *_: Laps([lap()]),
        schedule=schedule(**{"1": "conventional", "2": "sprint_qualifying"}),
    )
    assert [(o.round, o.rows) for o in outcomes] == [(1, 1), (2, 1)]
    assert store.exists(
        "bronze/table=session_laps/season=2024/round=01/session=fp2/session_laps.parquet"
    )
    assert store.exists(
        "bronze/table=session_laps/season=2024/round=02/session=sprint/session_laps.parquet"
    )


def test_backfill_resumes_over_what_is_already_in_bronze(store):
    args = dict(
        cache_dir=None,
        run_id="run-1",
        loader=lambda *_: Laps([lap()]),
        schedule=schedule(**{"1": "conventional"}),
    )
    backfill(store, 2024, **args)
    again = backfill(store, 2024, **args)
    assert again[0].skipped == "already in bronze"
    assert again[0].rows == 0


def test_a_season_in_progress_stops_at_the_last_race_held():
    events = [
        {"RoundNumber": 1, "EventFormat": "conventional", "EventDate": date(2025, 3, 16)},
        {"RoundNumber": 2, "EventFormat": "sprint_qualifying", "EventDate": date(2025, 3, 23)},
        {"RoundNumber": 3, "EventFormat": "conventional", "EventDate": date(2025, 4, 6)},
    ]
    assert held_events(events, date(2025, 3, 30)) == {1: "conventional", 2: "sprint_qualifying"}
