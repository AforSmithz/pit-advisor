from datetime import datetime

import polars as pl
import pytest

from pitadvisor.features import grid

QUALIFIED = {
    code: index + 1 for index, code in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"])
}
CALENDAR = [(2024, 1), (2024, 2), (2024, 3)]


def frames(rows):
    sanctions = pl.DataFrame(
        [
            {
                "season": row["season"],
                "round": row["round"],
                "document": f"doc-{index}",
                "entry": 0,
                "kind": row["kind"],
                "positions": row.get("positions"),
            }
            for index, row in enumerate(rows)
        ],
        schema={
            "season": pl.Int64,
            "round": pl.Int64,
            "document": pl.String,
            "entry": pl.Int64,
            "kind": pl.String,
            "positions": pl.Int64,
        },
    )
    incidents = pl.DataFrame(
        [
            {
                "season": row["season"],
                "round": row["round"],
                "document": f"doc-{index}",
                "entry": 0,
                "car": row["car"],
                "session": row["session"],
                "issued": row.get("issued", datetime(row["season"], 3, row["round"], 12)),
                "outcome": row.get("outcome", "Drop of grid positions."),
            }
            for index, row in enumerate(rows)
        ]
    )
    return sanctions, incidents


RESULTS = pl.DataFrame(
    {
        "season": [2024] * 6 + [2023] * 1,
        "round": [1] * 6 + [1],
        "car_number": [1, 2, 3, 4, 5, 6, 1],
        "driver_code": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "OLD"],
    }
)


def test_a_sanction_is_resolved_to_the_driver_in_that_seasons_car():
    sanctions, incidents = frames(
        [
            {
                "season": 2024,
                "round": 1,
                "kind": "grid_drop",
                "positions": 3,
                "car": 1,
                "session": "Qualifying",
            }
        ]
    )
    found = grid.penalties(sanctions, incidents, RESULTS)
    assert found["driver_code"].to_list() == ["AAA"]
    assert found["next_event"].to_list() == [False]


def test_a_race_offence_is_carried_whatever_the_wording():
    sanctions, incidents = frames(
        [
            {
                "season": 2024,
                "round": 1,
                "kind": "grid_drop",
                "positions": 5,
                "car": 2,
                "session": "Race",
                "outcome": "Drop of 5 grid positions.",
            }
        ]
    )
    assert grid.penalties(sanctions, incidents, RESULTS)["next_event"].to_list() == [True]


@pytest.mark.parametrize(
    ("season", "session", "sprint_only"),
    [
        (2024, "Sprint Qualifying", True),
        (2023, "Sprint Shootout", True),
        (2021, "Sprint Qualifying", False),
        (2024, "Sprint", False),
    ],
)
def test_sprint_qualifying_is_served_on_the_sprint_grid_from_2023(season, session, sprint_only):
    sanctions, incidents = frames(
        [
            {
                "season": season,
                "round": 1,
                "kind": "grid_drop",
                "positions": 3,
                "car": 3,
                "session": session,
            }
        ]
    )
    results = RESULTS.with_columns(pl.lit(season).cast(pl.Int64).alias("season"))
    found = grid.penalties(sanctions, incidents, results)
    assert found["sprint_only"].to_list() == [sprint_only]


def test_no_sanctions_is_an_empty_frame_not_a_crash():
    found = grid.penalties(None, None, RESULTS)
    assert found.height == 0
    assert set(found.columns) == set(grid.COLUMNS)


def frame(rows):
    return pl.DataFrame(
        [
            {
                "season": 2024,
                "round": row.get("round", 1),
                "driver_code": row["code"],
                "kind": row.get("kind", "grid_drop"),
                "positions": row.get("positions"),
                "issued": datetime(2024, 3, row.get("round", 1), row.get("hour", 12)),
                "session": row.get("session", "Qualifying"),
                "next_event": row.get("next_event", False),
                "sprint_only": row.get("sprint_only", False),
            }
            for row in rows
        ],
        schema=grid.SCHEMA,
    )


EVERYONE = {key: set(QUALIFIED) for key in CALENDAR}


def test_this_weekends_drop_is_served_this_weekend():
    found = frame([{"code": "AAA", "positions": 3}])
    served = grid.due(found, CALENDAR, EVERYONE, (2024, 1), set(QUALIFIED))
    assert [item.driver_code for item in served] == ["AAA"]
    assert grid.due(found, CALENDAR, EVERYONE, (2024, 2), set(QUALIFIED)) == []


def test_a_carried_drop_lands_on_the_next_race_the_driver_starts():
    found = frame([{"code": "BBB", "positions": 5, "next_event": True, "session": "Race"}])
    missed = {(2024, 1): set(QUALIFIED), (2024, 2): set(QUALIFIED) - {"BBB"}}
    assert grid.due(found, CALENDAR, EVERYONE, (2024, 1), set(QUALIFIED)) == []
    assert [
        i.driver_code for i in grid.due(found, CALENDAR, EVERYONE, (2024, 2), set(QUALIFIED))
    ] == ["BBB"]
    assert grid.due(found, CALENDAR, missed, (2024, 2), set(QUALIFIED) - {"BBB"}) == []
    assert [
        i.driver_code for i in grid.due(found, CALENDAR, missed, (2024, 3), set(QUALIFIED))
    ] == ["BBB"]


def test_a_sprint_grid_drop_never_touches_sundays_grid():
    found = frame([{"code": "CCC", "positions": 3, "sprint_only": True}])
    assert grid.due(found, CALENDAR, EVERYONE, (2024, 1), set(QUALIFIED)) == []


def test_a_drop_moves_the_car_back_and_everyone_behind_up():
    served = [grid.Penalty(driver_code="AAA", kind="grid_drop", positions=3, order=0)]
    slots = grid.starting_grid(QUALIFIED, served)
    assert sorted(slots, key=slots.get) == ["BBB", "CCC", "DDD", "AAA", "EEE", "FFF"]


def test_a_drop_longer_than_the_field_ends_at_the_back():
    served = [grid.Penalty(driver_code="EEE", kind="grid_drop", positions=10, order=0)]
    slots = grid.starting_grid(QUALIFIED, served)
    assert slots["EEE"] == 6


def test_back_of_grid_starters_line_up_by_where_they_qualified():
    served = [
        grid.Penalty(driver_code="DDD", kind="back_of_grid", positions=None, order=0),
        grid.Penalty(driver_code="BBB", kind="back_of_grid", positions=None, order=1),
    ]
    slots = grid.starting_grid(QUALIFIED, served)
    assert sorted(slots, key=slots.get) == ["AAA", "CCC", "EEE", "FFF", "BBB", "DDD"]


def test_a_drop_is_counted_against_the_whole_order_before_anyone_goes_to_the_back():
    """Abu Dhabi 2021 style: a car sent to the back still occupies its slot when a drop is
    counted, so the dropped car lands in front of the back-of-grid group, not among it."""
    served = [
        grid.Penalty(driver_code="BBB", kind="back_of_grid", positions=None, order=0),
        grid.Penalty(driver_code="AAA", kind="grid_drop", positions=2, order=1),
    ]
    slots = grid.starting_grid(QUALIFIED, served)
    assert sorted(slots, key=slots.get) == ["CCC", "AAA", "DDD", "EEE", "FFF", "BBB"]


def test_agreement_counts_a_grid_it_rebuilt_exactly():
    found = frame([{"code": "AAA", "positions": 3}])
    results = pl.DataFrame(
        {
            "season": [2024] * 6,
            "round": [1] * 6,
            "driver_code": ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"],
            "grid": [4, 1, 2, 3, 5, 0],
        }
    )
    measured = grid.agreement(found, {(2024, 1): QUALIFIED}, results, [(2024, 1)])
    assert measured.races == 1
    assert measured.races_matched == 1
    # the pit-lane starter is left out of the comparison rather than counted as a miss
    assert measured.slots == 5
    assert measured.slots_matched == 5
    assert measured.quali_slots_matched < 5


def session_laps(times: dict[str, list[tuple[int, int]]]) -> pl.DataFrame:
    """times maps a driver to (session clock in seconds, lap time in millis) pairs."""
    return pl.DataFrame(
        [
            {
                "season": 2024,
                "round": 6,
                "driver_code": code,
                "session_time_millis": clock * 1000,
                "lap_time_millis": lap,
                "is_deleted": False,
            }
            for code, laps in times.items()
            for clock, lap in laps
        ]
    )


def test_a_knockout_ranks_by_the_segment_reached_before_the_lap_time():
    # three segments separated by long silences; CCC set the quickest lap of all in the first
    # segment and went no further, so he starts behind both cars that reached the last one
    laps = session_laps(
        {
            "AAA": [(100, 90_500), (1_000, 90_200), (2_000, 89_900)],
            "BBB": [(110, 90_600), (1_010, 90_300), (2_010, 90_000)],
            "CCC": [(120, 89_000)],
            "DDD": [(130, 91_000), (1_020, 90_900)],
        }
    )
    order = grid.knockout(laps)
    assert grid.knockout_grid(order) == {"AAA": 1, "BBB": 2, "DDD": 3, "CCC": 4}
    reach = dict(zip(order["driver_code"], order["reach"], strict=True))
    assert reach == {"AAA": 3, "BBB": 3, "DDD": 2, "CCC": 1}


def test_a_knockout_reading_is_a_percent_off_the_quickest_three():
    laps = session_laps(
        {
            "AAA": [(100, 90_000), (1_000, 90_000), (2_000, 90_000)],
            "BBB": [(110, 90_000), (1_010, 90_000), (2_010, 90_000)],
            "CCC": [(120, 90_000), (1_020, 90_900)],
            "DDD": [(130, 91_800)],
        }
    )
    percent = dict(zip(*grid.knockout(laps).select("driver_code", "percent"), strict=True))
    assert percent["AAA"] == pytest.approx(0.0)
    assert percent["DDD"] == pytest.approx(2.0)


def test_a_session_with_too_little_in_it_gives_no_order():
    laps = session_laps({"AAA": [(100, 90_000)]})
    assert grid.knockout(laps).height == 0
