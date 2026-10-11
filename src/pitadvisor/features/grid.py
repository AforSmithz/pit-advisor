# pyright: reportUnknownMemberType=false
from collections.abc import Mapping, Set

import numpy as np
import polars as pl
from pydantic import BaseModel

KINDS = ("grid_drop", "back_of_grid")
SPRINT_GRID_FROM = 2023
COLUMNS = (
    "season",
    "round",
    "driver_code",
    "kind",
    "positions",
    "issued",
    "session",
    "next_event",
    "sprint_only",
)
SCHEMA: dict[str, type[pl.DataType]] = {
    "season": pl.Int64,
    "round": pl.Int64,
    "driver_code": pl.String,
    "kind": pl.String,
    "positions": pl.Int64,
    "issued": pl.Datetime,
    "session": pl.String,
    "next_event": pl.Boolean,
    "sprint_only": pl.Boolean,
}


class Penalty(BaseModel, frozen=True):
    driver_code: str
    kind: str
    positions: int | None
    order: int


def empty() -> pl.DataFrame:
    return pl.DataFrame(schema=SCHEMA)


def seated(frame: pl.DataFrame, results: pl.DataFrame) -> pl.DataFrame:
    """Adds driver_code to rows that name a car. The race being forecast has no result yet, so
    its car numbers come from the latest round of the same season that has one."""
    seats = (
        results.select(
            pl.col("season").cast(pl.Int64),
            pl.col("round").cast(pl.Int64),
            pl.col("car_number").cast(pl.Int64).alias("car"),
            "driver_code",
        )
        .drop_nulls()
        .unique(subset=["season", "round", "car"])
        .sort("round")
    )
    cars = (
        frame.with_columns(
            pl.col("season").cast(pl.Int64),
            pl.col("round").cast(pl.Int64),
            pl.col("car").cast(pl.Int64, strict=False),
        )
        .drop_nulls("car")
        .sort("round")
    )
    return cars.join_asof(
        seats, on="round", by=["season", "car"], strategy="backward", check_sortedness=False
    )


def penalties(
    sanctions: pl.DataFrame | None, incidents: pl.DataFrame | None, results: pl.DataFrame
) -> pl.DataFrame:
    """Grid sanctions resolved to a driver code and to the race they are served at. The
    stewards name the car, and the car number is only unique inside one season's entry list."""
    if sanctions is None or incidents is None or not sanctions.height:
        return empty()
    charged = (
        sanctions.filter(pl.col("kind").is_in(KINDS))
        .select("season", "round", "document", "entry", "kind", "positions")
        .join(
            incidents.select(
                "season", "round", "document", "entry", "car", "session", "issued", "outcome"
            ),
            on=["season", "round", "document", "entry"],
            how="inner",
        )
    )
    found = (
        seated(charged, results)
        .drop_nulls("driver_code")
        .with_columns(
            # "the next Race in which the driver participates" is this Sunday's race when the
            # offence came in practice, qualifying or the sprint. only a race offence carries
            # a power unit change is filed with no session at all
            next_event=(pl.col("session") == "Race").fill_null(False),
            # from 2023 the sprint has its own qualifying, and what happens there is served on
            # the sprint grid. in 2021 "sprint qualifying" was the sprint, which set sunday's grid
            sprint_only=(
                pl.col("outcome").str.contains(r"(?i)\bfor the sprint\b")
                | (
                    pl.col("session").is_in(["Sprint Shootout", "Sprint Qualifying"])
                    & (pl.col("season") >= SPRINT_GRID_FROM)
                )
            ).fill_null(False),
        )
        .unique(subset=["season", "round", "driver_code", "kind", "positions", "issued"])
    )
    if not found.height:
        return empty()
    return found.select(
        pl.col("season").cast(pl.Int64),
        pl.col("round").cast(pl.Int64),
        "driver_code",
        "kind",
        pl.col("positions").cast(pl.Int64),
        pl.col("issued").cast(pl.Datetime),
        "session",
        "next_event",
        "sprint_only",
    )


def due(
    found: pl.DataFrame,
    calendar: list[tuple[int, int]],
    entered: Mapping[tuple[int, int], Set[str]],
    target: tuple[int, int],
    field: Set[str],
) -> list[Penalty]:
    """What is served on this race's grid: this weekend's own sanctions, plus anything carried
    from an earlier race to the next one the driver actually starts. `calendar` is every
    event in date order; `field` is who is entered here, since an unrun race has no result."""
    if not found.height or target not in calendar:
        return []
    position = calendar.index(target)
    chosen: list[Penalty] = []
    rows = found.sort("issued").iter_rows(named=True)
    for order, row in enumerate(rows):
        code = str(row["driver_code"])
        if code not in field or bool(row["sprint_only"]):
            continue
        origin = (int(row["season"]), int(row["round"]))
        if not bool(row["next_event"]):
            if origin != target:
                continue
        else:
            if origin not in calendar or calendar.index(origin) >= position:
                continue
            later = calendar[calendar.index(origin) + 1 : position]
            # served at the first race the driver starts after the offence, not the next race
            # on the calendar, so a missed weekend carries it on
            if any(code in entered.get(event, set()) for event in later):
                continue
        positions = row["positions"]
        chosen.append(
            Penalty(
                driver_code=code,
                kind=str(row["kind"]),
                positions=int(positions) if positions is not None else None,
                order=order,
            )
        )
    return chosen


def starting_grid(qualified: Mapping[str, int], served: list[Penalty]) -> dict[str, int]:
    """Qualifying order, then each drop counted against the whole order in the order it was
    handed down, then everyone sent to the back lined up behind the field by where they
    qualified, which is how the stewards build the grid."""
    order = sorted(qualified, key=lambda code: qualified[code])
    back: set[str] = set()
    for item in sorted(served, key=lambda penalty: penalty.order):
        if item.driver_code not in order:
            continue
        if item.kind == "back_of_grid" or item.positions is None:
            back.add(item.driver_code)
            continue
        slot = order.index(item.driver_code)
        order.pop(slot)
        order.insert(min(slot + item.positions, len(order)), item.driver_code)
    front = [code for code in order if code not in back]
    rear = sorted(back, key=lambda code: qualified[code])
    return {code: index + 1 for index, code in enumerate([*front, *rear])}


class Agreement(BaseModel, frozen=True):
    races: int
    races_matched: int
    slots: int
    slots_matched: int
    quali_slots_matched: int
    mismatched: list[tuple[int, int]]


def agreement(
    found: pl.DataFrame,
    qualified: Mapping[tuple[int, int], Mapping[str, int]],
    results: pl.DataFrame,
    calendar: list[tuple[int, int]],
) -> Agreement:
    """How often the derived grid is the grid the race actually started from. Pit-lane starts
    and cars with no qualifying time are left out: neither is knowable from the order plus
    the stewards' sanctions on Saturday night."""
    entered: dict[tuple[int, int], set[str]] = {}
    actual: dict[tuple[int, int], dict[str, int]] = {}
    for row in results.drop_nulls(["driver_code", "grid"]).iter_rows(named=True):
        key = (int(row["season"]), int(row["round"]))
        entered.setdefault(key, set()).add(str(row["driver_code"]))
        if int(row["grid"]) > 0:
            actual.setdefault(key, {})[str(row["driver_code"])] = int(row["grid"])
    races = matched = slots = slots_matched = quali_matched = 0
    mismatched: list[tuple[int, int]] = []
    for key in calendar:
        order = qualified.get(key)
        started = actual.get(key)
        if not order or not started:
            continue
        derived = starting_grid(order, due(found, calendar, entered, key, entered.get(key, set())))
        compared = [code for code in started if code in derived]
        derived_rank = _ranks(derived, compared)
        quali_rank = _ranks(order, compared)
        actual_rank = _ranks(started, compared)
        hits = sum(derived_rank[code] == actual_rank[code] for code in compared)
        races += 1
        slots += len(compared)
        slots_matched += hits
        quali_matched += sum(quali_rank[code] == actual_rank[code] for code in compared)
        if hits == len(compared):
            matched += 1
        else:
            mismatched.append(key)
    return Agreement(
        races=races,
        races_matched=matched,
        slots=slots,
        slots_matched=slots_matched,
        quali_slots_matched=quali_matched,
        mismatched=mismatched,
    )


def _ranks(slots: Mapping[str, int], codes: list[str]) -> dict[str, int]:
    """Relative order among the cars being compared, so one pit-lane starter left out does not
    shift every slot behind him by one."""
    ranked = sorted(codes, key=lambda code: slots[code])
    return {code: index for index, code in enumerate(ranked)}


KNOCKOUT_COLUMNS = ("season", "round", "driver_code", "reach", "best_millis", "percent")
KNOCKOUT_SCHEMA: dict[str, type[pl.DataType]] = {
    "season": pl.Int64,
    "round": pl.Int64,
    "driver_code": pl.String,
    "reach": pl.Int64,
    "best_millis": pl.Float64,
    "percent": pl.Float64,
}
SEGMENTS = 3
TRIM = 3


def knockout(laps: pl.DataFrame) -> pl.DataFrame:
    """One knockout qualifying session as the format ranks it: the furthest segment a driver
    set a valid lap in, then the best valid lap in that segment. FastF1 does not label the
    segments, so the session is split at its two longest silences."""
    timed = laps.filter(
        pl.col("session_time_millis").is_not_null()
        & pl.col("lap_time_millis").is_not_null()
        & ~pl.col("is_deleted")
    ).sort("session_time_millis")
    if timed.height < SEGMENTS or timed["driver_code"].n_unique() < 2:
        return pl.DataFrame(schema=KNOCKOUT_SCHEMA)
    clock = timed["session_time_millis"].to_numpy().astype(float)
    gaps = np.diff(clock)
    cuts = sorted(float(clock[i + 1]) for i in np.argsort(gaps)[-(SEGMENTS - 1) :])
    segment = pl.lit(1, dtype=pl.Int64)
    for cut in cuts:
        segment = segment + (pl.col("session_time_millis") >= cut).cast(pl.Int64)
    segmented = timed.with_columns(segment.alias("segment"))
    reached = segmented.group_by("driver_code").agg(pl.col("segment").max().alias("reach"))
    best = (
        segmented.join(reached, on="driver_code")
        .filter(pl.col("segment") == pl.col("reach"))
        .group_by("driver_code", "reach")
        .agg(pl.col("lap_time_millis").min().cast(pl.Float64).alias("best_millis"))
    )
    fastest = segmented.group_by("driver_code").agg(pl.col("lap_time_millis").min())
    benchmark = float(np.sort(fastest["lap_time_millis"].to_numpy().astype(float))[:TRIM].mean())
    first = timed.row(0, named=True)
    return (
        best.join(fastest.rename({"lap_time_millis": "own_best"}), on="driver_code")
        .with_columns(
            pl.lit(int(first["season"])).cast(pl.Int64).alias("season"),
            pl.lit(int(first["round"])).cast(pl.Int64).alias("round"),
            (100.0 * (pl.col("own_best").cast(pl.Float64) - benchmark) / benchmark).alias(
                "percent"
            ),
            pl.col("reach").cast(pl.Int64),
        )
        .select(KNOCKOUT_COLUMNS)
        .sort(["reach", "best_millis"], descending=[True, False])
    )


def knockout_grid(order: pl.DataFrame) -> dict[str, int]:
    ranked = order.sort(["reach", "best_millis"], descending=[True, False])
    return {str(code): index + 1 for index, code in enumerate(ranked["driver_code"].to_list())}
