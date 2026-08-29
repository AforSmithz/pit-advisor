# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import polars as pl
from pydantic import BaseModel

# the grid has held twenty cars for every season in the lake, and a classified position
# never exceeds it. anything wider is a season this model has not seen
FIELD = 20
# a driver starting eleventh and one starting twelfth are the same problem, so neighbouring
# slots pool. without it a twenty way split of two thousand rows is a hundred rows a bucket
BANDWIDTH = 1.6
# enough prior mass to keep a slot nobody has ever occupied off zero, not enough to matter
# once a few hundred real rows land in it
PRIOR = 2.0
COLUMNS = ("season", "round", "race_date", "driver_code", "grid", "position", "points")


class Entries(BaseModel, frozen=True):
    """One race's starters with the three features a baseline is allowed to see."""

    season: int
    round: int
    driver_code: list[str]
    grid: list[int]
    # None is a driver the history cannot rank: a debutant, or anyone before the first race
    # in the window. He predicts as the back of the field and trains nothing
    standings: list[int | None]
    last_race: list[int | None]


def _slot(value: pl.Expr) -> pl.Expr:
    # a pit lane start is reported as grid zero, which is the back of the field, not the front
    return pl.when(value < 1).then(FIELD).otherwise(value.clip(1, FIELD))


def entries(results: pl.DataFrame, season: int, round_: int) -> Entries:
    """Everything known about a race's starters before the lights go out. History is every
    race that finished earlier, so nothing here can see the race being predicted."""
    field = results.filter((pl.col("season") == season) & (pl.col("round") == round_))
    if not field.height:
        raise NoHistoryError(f"{season} round {round_} has no result rows")
    when = field["race_date"][0]
    history = results.filter(pl.col("race_date") < when)
    standings = _standings(history, season)
    last = _last_race(history)
    codes = field["driver_code"].to_list()
    grid = field.select(_slot(pl.col("grid")).alias("slot"))["slot"].to_list()
    return Entries(
        season=season,
        round=round_,
        driver_code=[str(code) for code in codes],
        grid=[int(value) for value in grid],
        standings=[standings.get(str(code)) for code in codes],
        last_race=[last.get(str(code)) for code in codes],
    )


class NoHistoryError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


def _standings(history: pl.DataFrame, season: int) -> dict[str, int]:
    current = history.filter(pl.col("season") == season)
    # round one has no championship yet, so last season's final table is the standing
    table = current if current.height else history.filter(pl.col("season") == season - 1)
    if not table.height:
        return {}
    totals = (
        table.group_by("driver_code")
        .agg(pl.col("points").sum().alias("points"))
        .sort("points", descending=True)
    )
    return {
        str(row["driver_code"]): min(rank, FIELD)
        for rank, row in enumerate(totals.iter_rows(named=True), start=1)
    }


def _last_race(history: pl.DataFrame) -> dict[str, int]:
    if not history.height:
        return {}
    latest = (
        history.sort("race_date")
        .group_by("driver_code")
        .agg(pl.col("position").last().alias("position"))
    )
    return {
        str(row["driver_code"]): int(min(max(int(row["position"]), 1), FIELD))
        for row in latest.iter_rows(named=True)
        if row["position"] is not None
    }


class Lookup(BaseModel, frozen=True):
    """P(finish | one integer rank), smoothed along the rank axis and nothing else."""

    name: str
    bandwidth: float
    rows: int
    table: list[list[float]]

    def predict(self, rank: list[int | None]) -> np.ndarray:
        grid = np.asarray(self.table, dtype=float)
        filled = [FIELD if value is None else value for value in rank]
        index = np.clip(np.asarray(filled, dtype=int), 1, FIELD) - 1
        return grid[index]


def fit_lookup(
    rank: np.ndarray,
    finished: np.ndarray,
    name: str,
    bandwidth: float = BANDWIDTH,
    prior: float = PRIOR,
) -> Lookup:
    observed = np.clip(np.asarray(rank, dtype=int), 1, FIELD)
    outcome = np.clip(np.asarray(finished, dtype=int), 1, FIELD)
    counts = np.zeros((FIELD, FIELD))
    for slot, place in zip(observed, outcome, strict=True):
        counts[slot - 1, place - 1] += 1.0
    axis = np.arange(FIELD)
    kernel = np.exp(-np.abs(axis[:, None] - axis[None, :]) / bandwidth)
    pooled = kernel @ counts
    marginal = counts.sum(axis=0)
    marginal = marginal / marginal.sum() if marginal.sum() else np.full(FIELD, 1.0 / FIELD)
    smoothed = pooled + prior * marginal
    table = smoothed / smoothed.sum(axis=1, keepdims=True)
    return Lookup(
        name=name,
        bandwidth=bandwidth,
        rows=int(observed.shape[0]),
        table=[[float(value) for value in row] for row in table],
    )


FEATURES = ("grid", "standings", "last_race")


class Baselines(BaseModel, frozen=True):
    as_of: date
    lookups: dict[str, Lookup]

    def predict(self, field: Entries) -> dict[str, np.ndarray]:
        return {name: lookup.predict(getattr(field, name)) for name, lookup in self.lookups.items()}


def all_entries(results: pl.DataFrame) -> dict[tuple[int, int], Entries]:
    """A race's own features never change with the prediction date, because they are read off
    what happened before that race and nothing else. So they are built once for the lake."""
    known: dict[tuple[int, int], Entries] = {}
    for season, round_ in (
        results.select("season", "round").unique().sort("season", "round").iter_rows()
    ):
        try:
            known[(int(season), int(round_))] = entries(results, int(season), int(round_))
        except NoHistoryError:
            continue
    return known


def fit(
    results: pl.DataFrame,
    as_of: date,
    bandwidth: float = BANDWIDTH,
    known: dict[tuple[int, int], Entries] | None = None,
) -> Baselines:
    """Fitted on races strictly before as_of. §4.4: a baseline that has seen the race it is
    scored on is not a baseline, it is a leak with a low score."""
    history = results.filter(pl.col("race_date") < as_of).drop_nulls(["driver_code", "position"])
    table = known if known is not None else all_entries(history)
    rows: dict[str, list[int]] = {name: [] for name in FEATURES}
    outcomes: dict[str, list[int]] = {name: [] for name in FEATURES}
    for (season, round_), race in history.group_by("season", "round"):
        field = table.get((int(str(season)), int(str(round_))))
        if field is None:
            continue
        place = {
            str(row["driver_code"]): int(row["position"])
            for row in race.iter_rows(named=True)
            if row["position"] is not None
        }
        for index, code in enumerate(field.driver_code):
            if code not in place:
                continue
            for name in FEATURES:
                rank = getattr(field, name)[index]
                if rank is not None:
                    rows[name].append(rank)
                    outcomes[name].append(place[code])
    return Baselines(
        as_of=as_of,
        lookups={
            name: fit_lookup(
                np.asarray(rows[name], dtype=int),
                np.asarray(outcomes[name], dtype=int),
                name,
                bandwidth,
            )
            for name in FEATURES
        },
    )
