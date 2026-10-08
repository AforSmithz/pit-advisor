import json
from datetime import UTC, date, datetime, timedelta
from typing import Final

import polars as pl
from pydantic import BaseModel

from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.quality.checks import read_table
from pitadvisor.types import Layer

PLAN_KEY: Final = f"{Layer.CACHE}/weekend_plan.json"
# the schedule fires on thursday, so anything up to sunday is this weekend's race
WINDOW: Final = timedelta(days=4)


class NoCalendarError(RuntimeError):
    pass


class WeekendPlan(BaseModel, frozen=True):
    planned_at: datetime
    today: date
    race_week: bool
    season: int
    round: int
    race_name: str
    race_date: date
    circuit_id: str
    starts_at: datetime | None = None
    last_season: int | None
    last_round: int | None
    # where the session walk starts. a season's last race is followed by months without a race
    # week, so its session only lands when the next season's first race week walks back to it
    walk_from: int
    # the saturday run only does work once qualifying has landed and the race has not started:
    # before that it would publish thursday's forecast again, after it a forecast of the past
    after_qualifying: bool = False
    qualified: bool = False
    refresh: bool = False


def _qualified(store: ObjectStore, season: int, round_: int) -> bool:
    qualifying = read_table(store, Layer.BRONZE, "qualifying")
    if qualifying is None:
        return False
    return bool(
        qualifying.filter((pl.col("season") == season) & (pl.col("round") == round_)).height
    )


def plan(
    store: ObjectStore,
    today: date,
    now: datetime | None = None,
    after_qualifying: bool = False,
) -> WeekendPlan:
    now = now or datetime.now(UTC)
    races = read_table(store, Layer.BRONZE, "races")
    if races is None or not races.height:
        raise NoCalendarError("no races in bronze, the calendar has not landed")
    rows = sorted(races.to_dicts(), key=lambda row: row["race_date"])
    ahead = [row for row in rows if row["race_date"] >= today]
    behind = [row for row in rows if row["race_date"] < today]
    upcoming = ahead[0] if ahead else rows[-1]
    last = behind[-1] if behind else None
    race_week = bool(ahead) and upcoming["race_date"] <= today + WINDOW
    starts_at: datetime | None = upcoming.get("start_utc")
    if starts_at is not None and starts_at.tzinfo is None:
        starts_at = starts_at.replace(tzinfo=UTC)
    qualified = _qualified(store, int(upcoming["season"]), int(upcoming["round"]))
    started = starts_at is not None and starts_at <= now
    return WeekendPlan(
        planned_at=now,
        today=today,
        race_week=race_week,
        season=int(upcoming["season"]),
        round=int(upcoming["round"]),
        race_name=str(upcoming["race_name"]),
        race_date=upcoming["race_date"],
        circuit_id=str(upcoming["circuit_id"]),
        last_season=int(last["season"]) if last else None,
        last_round=int(last["round"]) if last else None,
        walk_from=int(last["season"]) if last else int(upcoming["season"]),
        starts_at=starts_at,
        after_qualifying=after_qualifying,
        qualified=qualified,
        refresh=race_week and (not after_qualifying or (qualified and not started)),
    )


def write(store: ObjectStore, weekend: WeekendPlan) -> str:
    return store.put(PLAN_KEY, json.dumps(weekend.model_dump(mode="json"), indent=2).encode())
