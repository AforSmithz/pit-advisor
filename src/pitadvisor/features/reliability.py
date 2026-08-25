# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import re
from datetime import date
from enum import StrEnum

import numpy as np
import polars as pl
from pydantic import BaseModel
from scipy import stats

# reliability moves slower than form does, a power unit spec lasts most of a season
HALF_LIFE_EVENTS = 10.0
# the shrinkage is worth exactly one field-average failure, which also keeps the gamma shape
# above 1: below that the posterior mean sits outside its own credible interval
PRIOR_FAILURES = 1.0
CREDIBLE = 0.90

COLUMNS = (
    "season",
    "round",
    "race_date",
    "driver_id",
    "constructor_id",
    "status",
    "laps_completed",
)


class Cause(StrEnum):
    # jolpica stopped naming causes after 2022, so the pooled hazard is the one that is
    # estimable in every season and the split is reported next to its coverage
    ANY = "any"
    POWER_UNIT = "power_unit"
    MECHANICAL = "mechanical"
    COLLISION = "collision"
    UNKNOWN = "unknown"


FINISHED = re.compile(r"^(finished|lapped|\+\d+ laps?)$")

# a car that never took the start was never exposed, and a disqualification is a stewards'
# decision rather than a car breaking
NOT_EXPOSED = frozenset({"did not start", "withdrew", "disqualified", "illness"})

CAUSES: dict[Cause, frozenset[str]] = {
    Cause.POWER_UNIT: frozenset(
        {
            "engine",
            "power unit",
            "power loss",
            "turbo",
            "electrical",
            "electronics",
            "exhaust",
            "cooling system",
            "water pressure",
            "water leak",
            "water pump",
            "oil leak",
            "oil pressure",
            "fuel pressure",
            "fuel pump",
            "fuel leak",
            "battery",
            "energy store",
            "overheating",
        }
    ),
    Cause.MECHANICAL: frozenset(
        {
            "gearbox",
            "transmission",
            "clutch",
            "driveshaft",
            "differential",
            "suspension",
            "brakes",
            "steering",
            "wheel",
            "wheel nut",
            "puncture",
            "hydraulics",
            "vibrations",
            "mechanical",
            "undertray",
            "front wing",
            "rear wing",
            "throttle",
            "seat",
        }
    ),
    Cause.COLLISION: frozenset(
        {
            "collision",
            "collision damage",
            "accident",
            "spun off",
            "damage",
        }
    ),
}

LOOKUP = {status: cause for cause, group in CAUSES.items() for status in group}


def classify(status: str) -> Cause | None:
    """None means the car was running at the end, or was never exposed at all."""
    plain = status.strip().lower()
    if FINISHED.match(plain) or plain in NOT_EXPOSED:
        return None
    return LOOKUP.get(plain, Cause.UNKNOWN)


def exposed(results: pl.DataFrame) -> pl.DataFrame:
    causes = [classify(str(status)) for status in results["status"]]
    return results.with_columns(
        pl.Series("cause", [None if cause is None else str(cause) for cause in causes]),
        pl.Series(
            "was_exposed", [str(s).strip().lower() not in NOT_EXPOSED for s in results["status"]]
        ),
    ).filter(pl.col("was_exposed"))


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


class Hazard(BaseModel, frozen=True):
    key: str
    cause: Cause
    per_lap: float
    interval_low: float
    interval_high: float
    weighted_failures: float
    weighted_laps: float


class ReliabilityFit(BaseModel, frozen=True):
    as_of: date
    half_life_events: float
    field_rates: dict[str, float]
    # the share of retirements that came with a named cause. below about a half the split is
    # decoration and only the pooled hazard means anything
    cause_coverage: float
    teams: list[Hazard]
    drivers: list[Hazard]
    events_used: int
    events_dropped: int


def _rate(failures: float, laps: float, prior: float) -> tuple[float, float, float]:
    """Gamma posterior on a Poisson rate, shrunk to the field with a pseudo-exposure."""
    prior_laps = PRIOR_FAILURES / prior if prior > 0.0 else max(laps, 1.0)
    shape = failures + PRIOR_FAILURES
    scale = 1.0 / (laps + prior_laps)
    tail = (1.0 - CREDIBLE) / 2.0
    return (
        float(shape * scale),
        float(stats.gamma.ppf(tail, a=shape, scale=scale)),
        float(stats.gamma.ppf(1.0 - tail, a=shape, scale=scale)),
    )


def _hazards(
    frame: pl.DataFrame, key: str, causes: tuple[Cause, ...], field: dict[str, float]
) -> list[Hazard]:
    laps = frame.group_by(key).agg(
        (pl.col("laps_completed") * pl.col("weight")).sum().alias("laps")
    )
    found: list[Hazard] = []
    for cause in causes:
        matching = (
            pl.col("cause").is_not_null() if cause is Cause.ANY else pl.col("cause") == str(cause)
        )
        failures = (
            frame.filter(matching).group_by(key).agg(pl.col("weight").sum().alias("failures"))
        )
        joined = laps.join(failures, on=key, how="left").with_columns(
            pl.col("failures").fill_null(0.0)
        )
        for row in joined.iter_rows(named=True):
            per_lap, low, high = _rate(row["failures"], row["laps"], field[str(cause)])
            found.append(
                Hazard(
                    key=str(row[key]),
                    cause=cause,
                    per_lap=per_lap,
                    interval_low=low,
                    interval_high=high,
                    weighted_failures=float(row["failures"]),
                    weighted_laps=float(row["laps"]),
                )
            )
    return sorted(found, key=lambda hazard: (hazard.cause, hazard.key))


def fit(results: pl.DataFrame, as_of: date, half_life: float = HALF_LIFE_EVENTS) -> ReliabilityFit:
    history = results.filter(pl.col("race_date") < as_of)
    dropped = results.height - history.height
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    running = exposed(history.join(ranked, on=["season", "round", "race_date"]))
    weighted = running.with_columns(
        pl.Series("weight", decay(running["events_ago"].to_numpy().astype(float), half_life))
    )

    total_laps = float((weighted["laps_completed"] * weighted["weight"]).sum())
    retirements = weighted.filter(pl.col("cause").is_not_null())
    field = {
        str(cause): float(
            (
                retirements
                if cause is Cause.ANY
                else retirements.filter(pl.col("cause") == str(cause))
            )["weight"].sum()
        )
        / max(total_laps, 1.0)
        for cause in Cause
    }
    named = float(retirements.filter(pl.col("cause") != str(Cause.UNKNOWN))["weight"].sum())
    retired = float(retirements["weight"].sum())
    return ReliabilityFit(
        as_of=as_of,
        half_life_events=half_life,
        field_rates=field,
        cause_coverage=named / retired if retired > 0.0 else 0.0,
        teams=_hazards(weighted, "constructor_id", tuple(Cause), field),
        # a driver carries the incidents, the car carries the failures
        drivers=_hazards(weighted, "driver_id", (Cause.COLLISION,), field),
        events_used=events.height,
        events_dropped=dropped,
    )
