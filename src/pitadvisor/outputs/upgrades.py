# pyright: reportUnknownMemberType=false
from datetime import UTC, datetime
from typing import Final

import polars as pl
from pydantic import BaseModel

from pitadvisor.features.assemble import EventContext
from pitadvisor.outputs.view_contracts import SCHEMA_VERSION

CATEGORIES: Final = ("performance", "circuit", "reliability", "unknown")


class Declared(BaseModel, frozen=True):
    constructor_id: str
    ordinal: int
    category: str
    text: str


class RoundUpdates(BaseModel, frozen=True):
    round: int
    # false when no submission was read for this round, which is not the same as "none brought"
    read: bool
    parts: dict[str, int]


class TeamUpdates(BaseModel, frozen=True):
    constructor_id: str
    rounds: list[RoundUpdates]


class UpgradesView(BaseModel, frozen=True):
    view: str = "upgrades_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    event: EventContext
    rounds: list[int]
    teams: list[TeamUpdates]
    this_event: list[Declared]


def upgrades_view(
    updates: pl.DataFrame | None,
    races: pl.DataFrame | None,
    context: EventContext,
    run_id: str,
    generated_at: datetime | None = None,
) -> UpgradesView:
    rounds = (
        sorted(
            int(value)
            for value in races.filter(
                (pl.col("season") == context.season) & (pl.col("round") <= context.round)
            )["round"].unique()
        )
        if races is not None
        else []
    )
    season = (
        updates.filter(pl.col("season") == context.season)
        if updates is not None
        else pl.DataFrame(schema={"round": pl.Int64, "constructor_id": pl.String})
    )
    teams: list[TeamUpdates] = []
    for team in sorted(str(value) for value in season["constructor_id"].unique()):
        mine = season.filter(pl.col("constructor_id") == team)
        rows: list[RoundUpdates] = []
        for number in rounds:
            here = mine.filter(pl.col("round") == number)
            counts = dict(here.group_by("category").len().iter_rows()) if here.height else {}
            rows.append(
                RoundUpdates(
                    round=number,
                    read=here.height > 0,
                    parts={name: int(counts.get(name, 0)) for name in CATEGORIES},
                )
            )
        teams.append(TeamUpdates(constructor_id=team, rounds=rows))
    current = season.filter(
        (pl.col("round") == context.round) & (pl.col("category") != "none")
    ).sort("constructor_id", "ordinal")
    return UpgradesView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        event=context,
        rounds=rounds,
        teams=teams,
        this_event=[
            Declared(
                constructor_id=str(row["constructor_id"]),
                ordinal=int(row["ordinal"]),
                category=str(row["category"]),
                text=" ".join(str(row["text"]).split()),
            )
            for row in current.iter_rows(named=True)
        ],
    )
