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


def control(*messages, season=2025, round_=20, session="race"):
    return pl.DataFrame(
        [
            {
                "season": season,
                "round": round_,
                "session": session,
                "ordinal": ordinal,
                "lap": None,
                "message": message,
            }
            for ordinal, message in enumerate(messages)
        ]
    )


RACES = pl.DataFrame(
    {"season": [2025, 2025], "round": [20, 21], "circuit_id": ["marina_bay", "interlagos"]}
)


def test_track_limits_and_moments_count_against_the_turn_they_name():
    seasons, turns = stewards.turn_history(
        control(
            "CAR 1 (VER) TIME 1:28.329 DELETED - TRACK LIMITS AT TURN 3 LAP 6 14:10:36",
            "CAR 44 (HAM) LAP DELETED - TRACK LIMITS AT TURN 3 (NEXT LAP PIT)",
            "CAR 4 (NOR) TRACK LIMITS TURN 7 LAP 12",
            "CAR 63 (RUS) SPUN AND CONTINUED AT TURN 7",
            "CAR 55 (SAI) OFF TRACK AND CONTINUED AT TURN 7",
            "GREEN LIGHT - PIT EXIT OPEN",
        ),
        RACES,
        "marina_bay",
    )
    assert seasons == [2025]
    assert [(t.number, t.track_limits, t.off_track) for t in turns] == [(3, 2, 0), (7, 1, 2)]


def test_an_incident_is_one_entry_carrying_the_last_word_on_it():
    _, turns = stewards.turn_history(
        control(
            "LAP 1 TURN 2 INCIDENT INVOLVING CARS 55 (SAI) AND 14 (ALO) NOTED - LEAVING THE TRACK "
            "AND GAINING AN ADVANTAGE",
            "FIA STEWARDS: LAP 1 TURN 2 INCIDENT INVOLVING CARS 55 (SAI) AND 14 (ALO) REVIEWED NO "
            "FURTHER INVESTIGATION - LEAVING THE TRACK AND GAINING AN ADVANTAGE",
            "LAP 1 TURN 1 INCIDENT NOTED",
        ),
        RACES,
        "marina_bay",
    )
    first, second = turns
    assert (first.number, len(first.incidents)) == (1, 1)
    assert first.incidents[0].drivers == []
    assert first.incidents[0].offence is None
    (incident,) = second.incidents
    assert incident.drivers == ["ALO", "SAI"]
    assert incident.kind == "leaving"
    assert incident.lap == 1
    assert incident.outcome == "Reviewed no further investigation"
    assert incident.offence == "Leaving the track and gaining an advantage"


def test_a_penalty_lands_on_the_incident_it_was_for_and_names_the_driver():
    _, turns = stewards.turn_history(
        control(
            "TURN 4 INCIDENT INVOLVING CARS 44 (HAM) AND 81 (PIA) NOTED - CAUSING A COLLISION",
            "FIA STEWARDS: TURN 4 INCIDENT INVOLVING CARS 44 (HAM) AND 81 (PIA) UNDER "
            "INVESTIGATION - CAUSING A COLLISION",
            "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 44 (HAM) - CAUSING A COLLISION",
            "FIA STEWARDS: 5 SECOND TIME PENALTY FOR CAR 16 (LEC) - SPEEDING IN THE PIT LANE",
        ),
        RACES,
        "marina_bay",
    )
    (incident,) = turns[0].incidents
    assert incident.kind == "collision"
    assert incident.outcome == "5 second time penalty for HAM"


def test_the_same_turn_and_cars_in_another_session_is_another_incident():
    rows = pl.concat(
        [
            control("TURN 9 INCIDENT INVOLVING CAR 4 (NOR) NOTED - IMPEDING", session="qualifying"),
            control("TURN 9 INCIDENT INVOLVING CAR 4 (NOR) NOTED - IMPEDING", session="race"),
        ]
    )
    _, turns = stewards.turn_history(rows, RACES, "marina_bay")
    assert [i.session for i in turns[0].incidents] == ["qualifying", "race"]


def test_only_the_circuit_asked_for_is_read():
    rows = pl.concat(
        [
            control("CAR 1 (VER) LAP DELETED - TRACK LIMITS AT TURN 3 LAP 2 14:00:00"),
            control("CAR 1 (VER) LAP DELETED - TRACK LIMITS AT TURN 5 LAP 2 14:00:00", round_=21),
        ]
    )
    _, turns = stewards.turn_history(rows, RACES, "interlagos")
    assert [t.number for t in turns] == [5]
    assert stewards.turn_history(rows, RACES, "monaco") == ([], [])
    assert stewards.turn_history(None, RACES, "interlagos") == ([], [])


def test_the_view_carries_the_turn_history():
    view = stewards.stewards_view(
        None,
        None,
        None,
        RESULTS,
        CONTEXT,
        "run-1",
        race_control=control("CAR 1 (VER) TRACK LIMITS TURN 7 LAP 12"),
        races=RACES,
    )
    assert view.turn_seasons == [2025]
    assert [t.number for t in view.turns] == [7]
