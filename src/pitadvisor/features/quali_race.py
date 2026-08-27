# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date
from enum import StrEnum

import numpy as np
import polars as pl
from pydantic import BaseModel

from pitadvisor.features.clean_pace import BENCHMARK_TRIM, SessionPace

HALF_LIFE_EVENTS = 5.0
CONFIDENCE_Z = 1.96
MIN_PAIRS_FOR_OFFSET = 4
# the median of n has an asymptotic error this much wider than the mean's
MEDIAN_EFFICIENCY = 1.2533

COLUMNS = (
    "season",
    "round",
    "race_date",
    "driver_code",
    "constructor_id",
    "q1_millis",
    "q2_millis",
    "q3_millis",
)


class Segment(StrEnum):
    Q1 = "q1"
    Q2 = "q2"
    Q3 = "q3"


SEGMENTS = (Segment.Q1, Segment.Q2, Segment.Q3)
REFERENCE = Segment.Q3


class NoQualifyingLapError(RuntimeError):
    def __init__(self, drivers: int) -> None:
        super().__init__(f"{drivers} entries left no timed qualifying lap")


class Evolution(BaseModel, frozen=True):
    segment: Segment
    # millis to subtract from a lap set in this segment to put it on reference-segment track
    offset_millis: float
    standard_error_millis: float
    pairs: int
    # a top team's q1 run is deliberately slow, so within-driver deltas are dispersed well
    # beyond measurement noise. published so nobody reads the offset as precise track rubber
    spread_millis: float


class DriverQualiRace(BaseModel, frozen=True):
    driver_code: str
    constructor_id: str
    segment: Segment
    # a q3 runner gets three shots at the best lap and a q1 exit gets one, so the minimum
    # flatters him by roughly a third of the lap-to-lap noise. small against the evolution
    # offset it sits inside, and in the opposite direction, but it does not vanish
    attempts: int
    raw_millis: float
    corrected_millis: float
    evolution_millis: float
    quali_percent_off: float
    race_percent_off: float | None = None
    # positive means further off the pace on sunday than on saturday
    delta: float | None = None
    delta_standard_error: float | None = None
    interval_low: float | None = None
    interval_high: float | None = None


class EventQualiRace(BaseModel, frozen=True):
    season: int
    round: int
    reference_segment: Segment = REFERENCE
    evolution: list[Evolution]
    benchmark_millis: float
    drivers: list[DriverQualiRace]
    matched: int
    unmatched: list[str]


def _within(frame: pl.DataFrame, earlier: Segment, later: Segment) -> np.ndarray:
    both = frame.filter(
        pl.col(f"{earlier}_millis").is_not_null() & pl.col(f"{later}_millis").is_not_null()
    )
    if not both.height:
        return np.zeros(0)
    return both[f"{earlier}_millis"].to_numpy().astype(float) - both[
        f"{later}_millis"
    ].to_numpy().astype(float)


def evolution(frame: pl.DataFrame, min_pairs: int = MIN_PAIRS_FOR_OFFSET) -> list[Evolution]:
    """Chained within-driver medians. A driver knocked out in Q1 never ran on the rubbered-in
    track the Q3 runners had, and comparing his raw lap to theirs charges him for it."""
    steps = ((Segment.Q2, Segment.Q3), (Segment.Q1, Segment.Q2))
    found = [
        Evolution(
            segment=REFERENCE,
            offset_millis=0.0,
            standard_error_millis=0.0,
            pairs=0,
            spread_millis=0.0,
        )
    ]
    running = 0.0
    for earlier, later in steps:
        delta = _within(frame, earlier, later)
        if len(delta) < min_pairs:
            found.append(
                Evolution(
                    segment=earlier,
                    offset_millis=running,
                    standard_error_millis=float("inf"),
                    pairs=len(delta),
                    spread_millis=0.0,
                )
            )
            continue
        step = float(np.median(delta))
        spread = float(1.4826 * np.median(np.abs(delta - step)))
        running += step
        previous = found[-1]
        error = MEDIAN_EFFICIENCY * spread / np.sqrt(len(delta))
        found.append(
            Evolution(
                segment=earlier,
                offset_millis=running,
                standard_error_millis=float(np.hypot(previous.standard_error_millis, error)),
                pairs=len(delta),
                spread_millis=spread,
            )
        )
    return sorted(found, key=lambda item: item.segment)


def _best(
    row: dict[str, object], offsets: dict[Segment, float]
) -> tuple[Segment, float, float, int]:
    timed = [
        (segment, float(str(row[f"{segment}_millis"])))
        for segment in SEGMENTS
        if row[f"{segment}_millis"] is not None
    ]
    segment, raw = min(timed, key=lambda item: item[1] - offsets[item[0]])
    return segment, raw, raw - offsets[segment], len(timed)


def fit_event(
    qualifying: pl.DataFrame,
    pace: SessionPace | None = None,
    trim: int = BENCHMARK_TRIM,
) -> EventQualiRace:
    timed = qualifying.filter(
        pl.col("q1_millis").is_not_null()
        | pl.col("q2_millis").is_not_null()
        | pl.col("q3_millis").is_not_null()
    )
    if not timed.height:
        raise NoQualifyingLapError(qualifying.height)

    steps = evolution(timed)
    offsets = {item.segment: item.offset_millis for item in steps}
    corrected = [(_best(row, offsets), row) for row in timed.iter_rows(named=True)]
    benchmark = float(np.sort([entry[0][2] for entry in corrected])[:trim].mean())

    race = {driver.driver_code: driver for driver in (pace.drivers if pace else [])}
    race_benchmark = pace.benchmark_millis if pace else 1.0
    quali_error = {item.segment: item.standard_error_millis for item in steps}

    drivers: list[DriverQualiRace] = []
    unmatched: list[str] = []
    for (segment, raw, value, attempts), row in corrected:
        code = str(row["driver_code"])
        percent = 100.0 * (value - benchmark) / benchmark
        found = race.get(code)
        if found is None:
            unmatched.append(code)
        # the two laps come from different sessions, so their errors do not share a term
        error = (
            float(
                np.hypot(
                    100.0 * quali_error[segment] / benchmark,
                    100.0 * found.standard_error_millis / race_benchmark,
                )
            )
            if found
            else None
        )
        delta = percent - found.percent_off_benchmark if found else None
        drivers.append(
            DriverQualiRace(
                driver_code=code,
                constructor_id=str(row["constructor_id"]),
                segment=segment,
                attempts=attempts,
                raw_millis=raw,
                corrected_millis=value,
                evolution_millis=offsets[segment],
                quali_percent_off=percent,
                race_percent_off=found.percent_off_benchmark if found else None,
                delta=delta,
                delta_standard_error=error,
                interval_low=delta - CONFIDENCE_Z * error if delta is not None and error else None,
                interval_high=delta + CONFIDENCE_Z * error if delta is not None and error else None,
            )
        )

    first = timed.row(0, named=True)
    return EventQualiRace(
        season=int(str(first["season"])),
        round=int(str(first["round"])),
        evolution=steps,
        benchmark_millis=benchmark,
        drivers=sorted(drivers, key=lambda item: item.corrected_millis),
        matched=sum(1 for driver in drivers if driver.delta is not None),
        unmatched=sorted(unmatched),
    )


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


class DriverTrend(BaseModel, frozen=True):
    driver_code: str
    delta: float
    standard_error: float
    interval_low: float
    interval_high: float
    events: int
    effective_events: float


class Trend(BaseModel, frozen=True):
    as_of: date
    half_life_events: float
    drivers: list[DriverTrend]
    events_used: int
    events_dropped: int


def trend(
    events: list[tuple[date, EventQualiRace]],
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
) -> Trend:
    """§5.3 wants the history, not the single weekend: one race is a strategy call, a
    standing gap between saturday and sunday is a property of the car."""
    history = sorted(((held, event) for held, event in events if held < as_of), key=lambda e: e[0])
    samples: dict[str, list[tuple[float, float]]] = {}
    for age, (_, event) in enumerate(reversed(history)):
        weight = float(decay(np.array([age]), half_life)[0])
        for driver in event.drivers:
            if driver.delta is None:
                continue
            samples.setdefault(driver.driver_code, []).append((driver.delta, weight))

    drivers: list[DriverTrend] = []
    for code, rows in sorted(samples.items()):
        value = np.array([delta for delta, _ in rows])
        weight = np.array([w for _, w in rows])
        mean = float(weight @ value / weight.sum())
        if len(value) > 1:
            spread = float(weight @ (value - mean) ** 2) / max(
                float(weight.sum()) - float((weight**2).sum()) / float(weight.sum()), 1e-9
            )
            error = float(np.sqrt(max(spread, 0.0) * float((weight**2).sum())) / weight.sum())
        else:
            error = float("inf")
        drivers.append(
            DriverTrend(
                driver_code=code,
                delta=mean,
                standard_error=error,
                interval_low=mean - CONFIDENCE_Z * error,
                interval_high=mean + CONFIDENCE_Z * error,
                events=len(rows),
                effective_events=float(weight.sum()),
            )
        )
    return Trend(
        as_of=as_of,
        half_life_events=half_life,
        drivers=drivers,
        events_used=len(history),
        events_dropped=len(events) - len(history),
    )
