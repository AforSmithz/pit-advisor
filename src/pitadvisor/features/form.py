# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import polars as pl
from pydantic import BaseModel

HALF_LIFE_EVENTS = 5.0
# only there to anchor each component's level, which teammate deltas leave free. heavier
# penalties shrink the contrasts too and the intervals stop covering
RIDGE = 0.05
# a team's two cars are only the same car until one of them has damage or a new floor
OUTLIER_MAD_MULTIPLE = 4.0
MIN_PAIRS_FOR_OUTLIERS = 8
CONFIDENCE_Z = 1.96

COLUMNS = ("season", "round", "race_date", "driver_code", "constructor_id", "value")


class NoPairingsError(RuntimeError):
    def __init__(self, events: int) -> None:
        super().__init__(f"{events} events left no teammate pair to compare")


class DriverForm(BaseModel, frozen=True):
    driver_code: str
    # teammate deltas only tie a driver to the drivers he shares a car lineage with. two
    # drivers in different components have no path between them and cannot be compared
    component: int
    # negative is quicker: the value being differenced is percent off the benchmark
    effect: float
    standard_error: float
    interval_low: float
    interval_high: float
    pairings: int
    events: int
    effective_events: float


class Contrast(BaseModel, frozen=True):
    faster: str
    slower: str
    delta: float
    standard_error: float
    interval_low: float
    interval_high: float


class FormFit(BaseModel, frozen=True):
    as_of: date
    half_life_events: float
    ridge: float
    drivers: list[DriverForm]
    pairs: int
    flagged_pairs: int
    components: int
    events_used: int
    events_dropped: int
    order: list[str]
    covariance: list[list[float]]

    def contrast(self, a: str, b: str) -> Contrast:
        """Two effects are anchored against each other, so their errors do not add in
        quadrature: the covariance term is most of the answer for a pair of teammates."""
        position = {code: index for index, code in enumerate(self.order)}
        matrix = np.array(self.covariance)
        weights = np.zeros(len(self.order))
        weights[position[a]] = 1.0
        weights[position[b]] = -1.0
        effect = {driver.driver_code: driver.effect for driver in self.drivers}
        delta = effect[a] - effect[b]
        error = float(np.sqrt(max(float(weights @ matrix @ weights), 0.0)))
        faster, slower = (a, b) if delta < 0 else (b, a)
        return Contrast(
            faster=faster,
            slower=slower,
            delta=delta,
            standard_error=error,
            interval_low=delta - CONFIDENCE_Z * error,
            interval_high=delta + CONFIDENCE_Z * error,
        )


def pairings(frame: pl.DataFrame) -> pl.DataFrame:
    left = frame.rename({"driver_code": "driver_a", "value": "value_a"})
    right = frame.rename({"driver_code": "driver_b", "value": "value_b"})
    joined = left.join(right, on=["season", "round", "race_date", "constructor_id"])
    return joined.filter(pl.col("driver_a") < pl.col("driver_b")).with_columns(
        (pl.col("value_a") - pl.col("value_b")).alias("delta")
    )


def flag_outliers(pairs: pl.DataFrame, multiple: float = OUTLIER_MAD_MULTIPLE) -> pl.DataFrame:
    if pairs.height < MIN_PAIRS_FOR_OUTLIERS:
        return pairs.with_columns(pl.lit(False).alias("is_outlier"))
    delta = pairs["delta"].to_numpy()
    centre = float(np.median(delta))
    spread = float(np.median(np.abs(delta - centre)))
    if spread == 0.0:
        return pairs.with_columns(pl.lit(False).alias("is_outlier"))
    limit = multiple * 1.4826 * spread
    return pairs.with_columns(((pl.col("delta") - centre).abs() > limit).alias("is_outlier"))


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def fit(
    frame: pl.DataFrame,
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
    ridge: float = RIDGE,
) -> FormFit:
    history = frame.filter(pl.col("race_date") < as_of)
    dropped = frame.height - history.height
    events = history.select("season", "round", "race_date").unique().sort("race_date")
    if events.height == 0:
        raise NoPairingsError(0)

    # age in events, not in days: a winter break is not five races of forgetting
    ranked = events.with_columns(
        (pl.len() - 1 - pl.col("race_date").rank("dense").cast(pl.Int64) + 1).alias("events_ago")
    )
    paired = flag_outliers(pairings(history.join(ranked, on=["season", "round", "race_date"])))
    kept = paired.filter(~pl.col("is_outlier"))
    if kept.height == 0:
        raise NoPairingsError(events.height)

    drivers = sorted(set(kept["driver_a"].to_list()) | set(kept["driver_b"].to_list()))
    index = {code: position for position, code in enumerate(drivers)}
    design = np.zeros((kept.height, len(drivers)))
    for row, (a, b) in enumerate(zip(kept["driver_a"], kept["driver_b"], strict=True)):
        design[row, index[a]] = 1.0
        design[row, index[b]] = -1.0
    target = kept["delta"].to_numpy()
    weight = decay(kept["events_ago"].to_numpy().astype(float), half_life)

    weighted = design * weight[:, None]
    normal = design.T @ weighted + ridge * np.eye(len(drivers))
    inverse = np.linalg.inv(normal)
    effect = inverse @ (weighted.T @ target)

    residual = target - design @ effect
    fitted = float(np.trace(inverse @ (design.T @ weighted)))
    dof = max(float(weight.sum()) - fitted, 1.0)
    variance = float(weight @ (residual**2)) / dof
    # the decay weights are not inverse variances, so the sandwich carries w squared
    squared = design * (weight**2)[:, None]
    covariance = variance * (inverse @ (design.T @ squared) @ inverse)
    error = np.sqrt(np.clip(np.diag(covariance), 0.0, None))

    counts = _per_driver(kept, weight)
    component = _components(kept, drivers)
    return FormFit(
        as_of=as_of,
        half_life_events=half_life,
        ridge=ridge,
        pairs=kept.height,
        flagged_pairs=int(paired["is_outlier"].sum()),
        components=len(set(component.values())),
        events_used=events.height,
        events_dropped=dropped,
        order=drivers,
        covariance=[[float(value) for value in row] for row in covariance],
        drivers=[
            DriverForm(
                driver_code=code,
                component=component[code],
                effect=float(effect[position]),
                standard_error=float(error[position]),
                interval_low=float(effect[position] - CONFIDENCE_Z * error[position]),
                interval_high=float(effect[position] + CONFIDENCE_Z * error[position]),
                pairings=counts[code].pairings,
                events=len(counts[code].events),
                effective_events=counts[code].effective,
            )
            for code, position in index.items()
        ],
    )


def _components(kept: pl.DataFrame, drivers: list[str]) -> dict[str, int]:
    parent = {code: code for code in drivers}

    def root(code: str) -> str:
        while parent[code] != code:
            parent[code] = parent[parent[code]]
            code = parent[code]
        return code

    for a, b in zip(kept["driver_a"], kept["driver_b"], strict=True):
        parent[root(a)] = root(b)
    labels = {name: number for number, name in enumerate(sorted({root(c) for c in drivers}))}
    return {code: labels[root(code)] for code in drivers}


class Tally(BaseModel):
    pairings: int = 0
    events: set[tuple[int, int]] = set()
    effective: float = 0.0


def _per_driver(kept: pl.DataFrame, weight: np.ndarray) -> dict[str, Tally]:
    counted: dict[str, Tally] = {}
    rows = kept.with_columns(pl.Series("weight", weight))
    for side in ("driver_a", "driver_b"):
        for row in rows.select(side, "season", "round", "weight").iter_rows(named=True):
            tally = counted.setdefault(row[side], Tally(events=set()))
            tally.pairings += 1
            tally.events.add((int(row["season"]), int(row["round"])))
            tally.effective += float(row["weight"])
    return counted
