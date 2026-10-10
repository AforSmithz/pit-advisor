# pyright: reportUnknownMemberType=false
from collections.abc import Mapping, Set

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


def penalties(
    sanctions: pl.DataFrame | None, incidents: pl.DataFrame | None, results: pl.DataFrame
) -> pl.DataFrame:
    """Grid sanctions resolved to a driver code and to the race they are served at. The
    stewards name the car, and the car number is only unique inside one season's entry list."""
    if sanctions is None or incidents is None or not sanctions.height:
        return empty()
    seats = results.select("season", "round", "car_number", "driver_code").drop_nulls()
    found = (
        sanctions.filter(pl.col("kind").is_in(KINDS))
        .select("season", "round", "document", "entry", "kind", "positions")
        .join(
            incidents.select(
                "season", "round", "document", "entry", "car", "session", "issued", "outcome"
            ),
            on=["season", "round", "document", "entry"],
            how="inner",
        )
        .join(
            seats,
            left_on=["season", "round", "car"],
            right_on=["season", "round", "car_number"],
            how="inner",
        )
        .with_columns(
            # "the next Race in which the driver participates" is this Sunday's race when the
            # offence came in practice, qualifying or the sprint. only a race offence carries
            next_event=pl.col("session") == "Race",
            # from 2023 the sprint has its own qualifying, and what happens there is served on
            # the sprint grid. in 2021 "sprint qualifying" was the sprint, which set sunday's grid
            sprint_only=pl.col("outcome").str.contains(r"(?i)\bfor the sprint\b")
            | (
                pl.col("session").is_in(["Sprint Shootout", "Sprint Qualifying"])
                & (pl.col("season") >= SPRINT_GRID_FROM)
            ),
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
