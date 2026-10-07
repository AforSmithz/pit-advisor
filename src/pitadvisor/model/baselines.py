# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import polars as pl
from pydantic import BaseModel

# the narrowest a grid gets. 2021 to 2025 ran twenty cars and 2026 added an eleventh team, so
# a race's width is its own entry count, never less than this
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
    width: int
    driver_code: list[str]
    grid: list[int]
    # None is a driver the history cannot rank: a debutant, or anyone before the first race
    # in the window. He predicts as the back of the field and trains nothing
    standings: list[int | None]
    last_race: list[int | None]


def _slot(value: pl.Expr, width: int) -> pl.Expr:
    # a pit lane start is reported as grid zero, which is the back of the field, not the front
    return pl.when(value < 1).then(width).otherwise(value.clip(1, width))


def entries(results: pl.DataFrame, season: int, round_: int) -> Entries:
    """Everything known about a race's starters before the lights go out. History is every
    race that finished earlier, so nothing here can see the race being predicted."""
    field = results.filter((pl.col("season") == season) & (pl.col("round") == round_))
    if not field.height:
        raise NoHistoryError(f"{season} round {round_} has no result rows")
    when = field["race_date"][0]
    width = max(FIELD, field.height)
    history = results.filter(pl.col("race_date") < when)
    standings = _standings(history, season, width)
    last = _last_race(history, width)
    codes = field["driver_code"].to_list()
    grid = field.select(_slot(pl.col("grid"), width).alias("slot"))["slot"].to_list()
    return Entries(
        season=season,
        round=round_,
        width=width,
        driver_code=[str(code) for code in codes],
        grid=[int(value) for value in grid],
        standings=[standings.get(str(code)) for code in codes],
        last_race=[last.get(str(code)) for code in codes],
    )


class NoHistoryError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


def _standings(history: pl.DataFrame, season: int, width: int) -> dict[str, int]:
    current = history.filter(pl.col("season") == season)
    # round one has no championship yet, so last season's final table is the standing
    table = current if current.height else history.filter(pl.col("season") == season - 1)
    if not table.height:
        return {}
    totals = (
        table.group_by("driver_code")
        .agg(pl.col("points").sum().alias("points"), pl.col("position").min().alias("best"))
        # ties break on countback the way the championship does, best finish first. without
        # it half the pointless field ranked in whatever order the group_by hashed them
        .sort(
            ["points", "best", "driver_code"],
            descending=[True, False, False],
            nulls_last=True,
        )
    )
    return {
        str(row["driver_code"]): min(rank, width)
        for rank, row in enumerate(totals.iter_rows(named=True), start=1)
    }


def _last_race(history: pl.DataFrame, width: int) -> dict[str, int]:
    if not history.height:
        return {}
    latest = (
        history.sort("race_date")
        .group_by("driver_code")
        .agg(pl.col("position").last().alias("position"))
    )
    return {
        str(row["driver_code"]): int(min(max(int(row["position"]), 1), width))
        for row in latest.iter_rows(named=True)
        if row["position"] is not None
    }


class Lookup(BaseModel, frozen=True):
    """P(finish | one integer rank), smoothed along the rank axis and nothing else."""

    name: str
    bandwidth: float
    rows: int
    table: list[list[float]]

    def predict(self, rank: list[int | None], width: int = FIELD) -> np.ndarray:
        grid = np.asarray(self.table, dtype=float)
        filled = [width if value is None else value for value in rank]
        index = np.clip(np.asarray(filled, dtype=int), 1, grid.shape[0]) - 1
        return grid[index]


def stretch(size: int, width: int) -> np.ndarray:
    """(size, width): how much of place i in a size car race lands on place k of a width car
    one, by the share of the field each covers. last of twenty is last of twenty two, not 20th
    of 22 with two places behind it nobody has ever finished in."""
    if size == width:
        return np.eye(width)
    low = np.arange(size)[:, None] / size
    high = (np.arange(size)[:, None] + 1) / size
    left = np.arange(width)[None, :] / width
    right = (np.arange(width)[None, :] + 1) / width
    return size * np.clip(np.minimum(high, right) - np.maximum(low, left), 0.0, None)


def fit_lookup(
    rank: np.ndarray,
    finished: np.ndarray,
    name: str,
    width: int = FIELD,
    bandwidth: float = BANDWIDTH,
    prior: float = PRIOR,
    sizes: np.ndarray | None = None,
) -> Lookup:
    ranks = np.asarray(rank, dtype=int)
    places = np.asarray(finished, dtype=int)
    fields = np.full(ranks.shape[0], width) if sizes is None else np.asarray(sizes, dtype=int)
    counts = np.zeros((width, width))
    for size in np.unique(fields):
        mine = fields == size
        raw = np.zeros((size, size))
        np.add.at(
            raw,
            (np.clip(ranks[mine], 1, size) - 1, np.clip(places[mine], 1, size) - 1),
            1.0,
        )
        spread = stretch(int(size), width)
        counts += raw if size == width else spread.T @ raw @ spread
    axis = np.arange(width)
    kernel = np.exp(-np.abs(axis[:, None] - axis[None, :]) / bandwidth)
    pooled = kernel @ counts
    marginal = counts.sum(axis=0)
    marginal = marginal / marginal.sum() if marginal.sum() else np.full(width, 1.0 / width)
    smoothed = pooled + prior * marginal
    table = smoothed / smoothed.sum(axis=1, keepdims=True)
    return Lookup(
        name=name,
        bandwidth=bandwidth,
        rows=int(ranks.shape[0]),
        table=[[float(value) for value in row] for row in table],
    )


FEATURES = ("grid", "standings", "last_race")


class Baselines(BaseModel, frozen=True):
    as_of: date
    lookups: dict[str, Lookup]

    def predict(self, field: Entries) -> dict[str, np.ndarray]:
        return {
            name: lookup.predict(getattr(field, name), field.width)
            for name, lookup in self.lookups.items()
        }


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
    width: int = FIELD,
) -> Baselines:
    """Fitted on races strictly before as_of. §4.4: a baseline that has seen the race it is
    scored on is not a baseline, it is a leak with a low score. width is the race about to be
    predicted, and every race in the history is stretched onto it by share of the field."""
    history = results.filter(pl.col("race_date") < as_of).drop_nulls(["driver_code", "position"])
    table = known if known is not None else all_entries(history)
    rows: dict[str, list[int]] = {name: [] for name in FEATURES}
    outcomes: dict[str, list[int]] = {name: [] for name in FEATURES}
    sizes: dict[str, list[int]] = {name: [] for name in FEATURES}
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
                    sizes[name].append(field.width)
    return Baselines(
        as_of=as_of,
        lookups={
            name: fit_lookup(
                np.asarray(rows[name], dtype=int),
                np.asarray(outcomes[name], dtype=int),
                name,
                width,
                bandwidth,
                sizes=np.asarray(sizes[name], dtype=int),
            )
            for name in FEATURES
        },
    )
