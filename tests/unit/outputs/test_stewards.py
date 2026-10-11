from datetime import date, datetime

import polars as pl
import pytest

from pitadvisor.features.assemble import EventContext
from pitadvisor.outputs import stewards

CONTEXT = EventContext(
    season=2026, round=3, circuit_id="marina_bay", race_name="Test GP", race_date=date(2026, 3, 9)
)
RESULTS = pl.DataFrame(
    {
        "season": [2026, 2026],
        "round": [2, 2],
        "car_number": [1, 44],
        "driver_code": ["NOR", "HAM"],
    }
)


def frames(rows):
    incidents = pl.DataFrame(
        [
            {
                "season": row["season"],
                "round": row["round"],
                "document": index,
                "entry": 0,
                "kind": "decision",
                "issued": datetime(2026, 3, row["round"], 12, index),
                "car": row["car"],
                "driver": row.get("driver", "Someone"),
                "competitor": "Team",
                "session": row.get("session", "Race"),
                "fact": row.get("fact", ""),
                "outcome": row.get("outcome", ""),
            }
            for index, row in enumerate(rows)
        ]
    )
    sanctions = pl.DataFrame(
        [
            {
                "season": row["season"],
                "round": row["round"],
                "document": index,
                "entry": 0,
                "ordinal": 0,
                "kind": row["sanction"],
                "seconds": row.get("seconds"),
                "positions": row.get("positions"),
                "text": row["sanction"],
            }
            for index, row in enumerate(rows)
        ],
        schema={
            "season": pl.Int64,
            "round": pl.Int64,
            "document": pl.Int64,
            "entry": pl.Int64,
            "ordinal": pl.Int64,
            "kind": pl.String,
            "seconds": pl.Int64,
            "positions": pl.Int64,
            "text": pl.String,
        },
    )
    articles = pl.DataFrame(
        [
            {
                "season": row["season"],
                "round": row["round"],
                "document": index,
                "entry": 0,
                "code": row["rule"],
                "book": "isc",
            }
            for index, row in enumerate(rows)
            if row.get("rule")
        ]
    )
    return incidents, sanctions, articles


@pytest.mark.parametrize(
    ("code", "fact", "offence"),
    [
        ("Appendix L, Chapter IV, Article 2 d)", "", "collision"),
        ("Appendix H, Article 2.5.5b", "", "yellow"),
        ("Appendix H, Article 2.5.5 b)", "", "yellow"),
        ("Article 12.2.1", "", "conduct"),
        ("Article 12.2.1 i)", "", "procedure"),
        (None, "Car 27 impeded Car 55 in turn 5", "impeding"),
        (None, "Something else entirely", None),
    ],
)
def test_a_ruling_is_filed_under_its_rule_or_what_its_fact_says(code, fact, offence):
    assert stewards.offence_of(code, fact) == offence


def test_this_weekends_rulings_carry_the_driver_the_car_had():
    incidents, sanctions, articles = frames(
        [
            {
                "season": 2026,
                "round": 3,
                "car": 1,
                "sanction": "time_penalty",
                "seconds": 5,
                "rule": "Appendix L, Chapter IV, Article 2 d)",
            },
            {"season": 2026, "round": 2, "car": 44, "sanction": "none", "rule": "Article 34.7"},
        ]
    )
    view = stewards.stewards_view(incidents, sanctions, articles, RESULTS, CONTEXT, "run-1")
    assert [(item.driver_code, item.offence) for item in view.rulings] == [("NOR", "collision")]
    assert view.rulings[0].sanction.outcome == "time"
    assert view.rulings[0].sanction.seconds == 5


def test_precedents_count_outcomes_and_split_time_penalties_by_size():
    incidents, sanctions, articles = frames(
        [
            {
                "season": 2026,
                "round": 2,
                "car": 1,
                "sanction": "time_penalty",
                "seconds": seconds,
                "rule": "Appendix L, Chapter IV, Article 2 d)",
            }
            for seconds in (5, 10, 10)
        ]
        + [
            {
                "season": 2026,
                "round": 2,
                "car": 44,
                "sanction": "reprimand",
                "rule": "Appendix L, Chapter IV, Article 2 d)",
            }
        ]
    )
    view = stewards.stewards_view(incidents, sanctions, articles, RESULTS, CONTEXT, "run-1")
    (collision,) = view.precedents
    assert collision.rulings == 4
    assert collision.outcomes["time"] == 3
    assert collision.outcomes["warning"] == 1
    assert collision.seconds == {"5": 1, "10": 2}
    assert view.total_rulings == 4


def test_no_incidents_is_an_empty_view_not_a_crash():
    view = stewards.stewards_view(None, None, None, RESULTS, CONTEXT, "run-1")
    assert view.rulings == []
    assert view.precedents == []
    assert view.total_rulings == 0
