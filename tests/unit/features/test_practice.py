from datetime import date

import polars as pl
import pytest

from pitadvisor.features import practice


def stint(code, session, base, laps=8, compound="MEDIUM", stint=2, season=2023, round_=5):
    rows = []
    for lap in range(1, laps + 1):
        rows.append(
            {
                "season": season,
                "round": round_,
                "session": session,
                "driver_code": code,
                "lap": lap,
                "stint": stint,
                "lap_in_stint": lap,
                "lap_time_millis": base + (20_000 if lap == laps else 0),
                "compound": compound,
                "track_status": "1",
                "pit_in": lap == laps,
                "pit_out": lap == 1,
                "is_deleted": False,
                "is_accurate": True,
            }
        )
    return rows


def frame(rows):
    return pl.DataFrame(rows)


def test_a_long_run_needs_five_laps_after_the_out_lap_and_drops_the_in_lap():
    runs = practice.long_runs(frame(stint("AAA", "fp2", 95_000, laps=8)))
    assert runs.height == 1
    assert runs["laps"][0] == 6
    assert runs["median_millis"][0] == 95_000
    assert practice.long_runs(frame(stint("AAA", "fp2", 95_000, laps=5))).height == 0


def test_a_push_lap_inside_a_run_is_dropped_by_the_window():
    rows = stint("AAA", "fp2", 95_000, laps=9)
    rows[3]["lap_time_millis"] = 120_000
    runs = practice.long_runs(frame(rows))
    assert runs["laps"][0] == 6


def schedule(sprint: bool) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [2023],
            "round": [5],
            "race_date": [date(2023, 5, 7)],
            "sprint_date": [date(2023, 5, 6) if sprint else None],
        },
        schema_overrides={"sprint_date": pl.Date},
    )


RESULTS = pl.DataFrame(
    {
        "season": [2023] * 4,
        "round": [5] * 4,
        "driver_code": ["AAA", "BBB", "CCC", "DDD"],
        "constructor_id": ["alpha", "alpha", "bravo", "bravo"],
    }
)


def test_a_team_reads_against_the_field_on_the_same_compound():
    rows = (
        stint("AAA", "fp2", 95_000)
        + stint("BBB", "fp2", 95_000)
        + stint("CCC", "fp2", 96_000)
        + stint("DDD", "fp2", 96_000)
        # a hard-tyre run is slower and must not drag alpha's offset with it
        + stint("AAA", "fp2", 99_000, compound="HARD", stint=3)
    )
    found = practice.offsets(frame(rows), RESULTS, schedule(False))
    offsets = dict(zip(found["constructor_id"], found["offset"], strict=True))
    assert offsets["alpha"] < offsets["bravo"]
    assert found["race_date"].to_list() == [date(2023, 5, 7)] * 2


def test_a_sprint_weekend_reads_fp1_and_a_normal_one_fp2():
    rows = stint("AAA", "fp1", 95_000) + stint("CCC", "fp1", 96_000)
    assert practice.offsets(frame(rows), RESULTS, schedule(True)).height == 2
    assert practice.offsets(frame(rows), RESULTS, schedule(False)).height == 0


def test_no_schedule_is_no_practice():
    assert practice.offsets(frame(stint("AAA", "fp2", 95_000)), RESULTS, None).height == 0
    assert set(practice.offsets(frame([]), RESULTS, schedule(False)).columns) == set(
        practice.COLUMNS
    )


@pytest.mark.parametrize("compound", ["INTERMEDIATE", "WET"])
def test_a_wet_run_is_not_a_long_run(compound):
    assert practice.long_runs(frame(stint("AAA", "fp2", 95_000, compound=compound))).height == 0
