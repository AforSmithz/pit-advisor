from datetime import timedelta

import polars as pl
import pytest

from pitadvisor.ingest.fastf1_session import (
    UnknownFormatError,
    ingest_session,
    millis,
    sessions_for,
    to_records,
)
from pitadvisor.types import SessionKey, SessionKind, Source

KEY = SessionKey(season=2024, round=5, session=SessionKind.RACE)
STAMP = {"run_id": "run-1", "ingested_at": None}


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
    from datetime import UTC, datetime

    return {"run_id": "run-1", "ingested_at": datetime(2024, 5, 5, tzinfo=UTC)}


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
    rows = to_records(
        Laps([lap(LapNumber=n, Stint=1.0 if n < 3 else 2.0) for n in (1, 2, 3, 4)]), KEY, stamped()
    )
    assert [row["lap_in_stint"] for row in rows] == [1, 2, 1, 2]


def test_lap_in_stint_is_per_driver():
    rows = to_records(
        Laps([lap(Driver="VER"), lap(Driver="NOR"), lap(Driver="VER", LapNumber=2.0)]),
        KEY,
        stamped(),
    )
    assert [row["lap_in_stint"] for row in rows] == [1, 1, 2]


def test_deleted_and_pit_flags_survive():
    rows = to_records(Laps([lap(Deleted=True, PitInTime=timedelta(seconds=1))]), KEY, stamped())
    assert rows[0]["is_deleted"] is True
    assert rows[0]["pit_in"] is True
    assert rows[0]["pit_out"] is False


def test_track_status_stays_a_string():
    rows = to_records(Laps([lap(TrackStatus="24")]), KEY, stamped())
    assert rows[0]["track_status"] == "24"


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
