import json
from datetime import UTC, date, datetime

import pytest

from pitadvisor.ingest.raw_store import write_bronze
from pitadvisor.ingest.schedule import PLAN_KEY, NoCalendarError, plan, write
from pitadvisor.quality.contracts import RaceRow
from pitadvisor.types import EventKey

STAMP = datetime(2026, 10, 1, tzinfo=UTC)
CALENDAR = ((1, date(2026, 9, 27)), (2, date(2026, 10, 11)), (3, date(2026, 10, 25)))


def calendar(store, rounds=CALENDAR):
    for round_, day in rounds:
        row = RaceRow(
            run_id="run-1",
            ingested_at=STAMP,
            season=2026,
            round=round_,
            race_name=f"Round {round_}",
            circuit_id=f"c{round_}",
            circuit_name="Ring",
            latitude=1.0,
            longitude=2.0,
            race_date=day,
        )
        write_bronze(store, "races", EventKey(season=2026, round=round_), [row])


def test_the_thursday_before_a_race_is_a_race_week(store):
    calendar(store)
    weekend = plan(store, date(2026, 10, 8), STAMP)
    assert weekend.race_week
    assert (weekend.season, weekend.round) == (2026, 2)
    assert (weekend.last_season, weekend.last_round) == (2026, 1)


def test_an_empty_week_between_races_is_not(store):
    calendar(store)
    weekend = plan(store, date(2026, 10, 15), STAMP)
    assert not weekend.race_week
    assert weekend.round == 3
    assert weekend.last_round == 2


def test_race_day_itself_still_counts(store):
    calendar(store)
    assert plan(store, date(2026, 10, 11), STAMP).race_week


def test_after_the_last_race_there_is_nothing_to_run(store):
    calendar(store)
    weekend = plan(store, date(2026, 12, 3), STAMP)
    assert not weekend.race_week
    assert (weekend.round, weekend.last_round) == (3, 3)


def test_no_calendar_is_an_error_not_a_quiet_skip(store):
    with pytest.raises(NoCalendarError):
        plan(store, date(2026, 10, 8), STAMP)


def test_the_plan_lands_where_the_state_machine_reads_it(store):
    calendar(store)
    write(store, plan(store, date(2026, 10, 8), STAMP))
    payload = json.loads(store.get(PLAN_KEY))
    assert PLAN_KEY == "cache/weekend_plan.json"
    assert payload["race_week"] is True
    assert payload["round"] == 2


def test_a_new_season_walks_back_to_the_last_one(store):
    calendar(store)
    later = RaceRow(
        run_id="run-1",
        ingested_at=STAMP,
        season=2027,
        round=1,
        race_name="Opener",
        circuit_id="c9",
        circuit_name="Ring",
        latitude=1.0,
        longitude=2.0,
        race_date=date(2027, 3, 14),
    )
    write_bronze(store, "races", EventKey(season=2027, round=1), [later])
    weekend = plan(store, date(2027, 3, 11), STAMP)
    assert weekend.race_week
    assert (weekend.season, weekend.round) == (2027, 1)
    # the 2026 finale's session never had a race week of its own after it
    assert weekend.walk_from == 2026
