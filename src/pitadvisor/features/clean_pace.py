# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from enum import StrEnum

import numpy as np
import polars as pl
from pydantic import BaseModel
from scipy import stats

from pitadvisor.types import SessionKind

OPENING_LAPS = 2
# a car this close is running in the dirty air of the one ahead, so its lap time is a
# measurement of the gap and not of its own pace
TRAFFIC_THRESHOLD_MILLIS = 1_500
REFERENCE_TYRE_AGE = 1
REFERENCE_LAPS_REMAINING = 0
# a wet lap is not a slow dry lap, it is a different regime. §5.5 owns wet pace
WET_COMPOUNDS = frozenset({"INTERMEDIATE", "WET"})
MIN_CLEAN_LAPS = 5
MIN_DRIVERS = 2
# the single fastest estimate is an extreme order statistic: on a race with few clean laps it
# came back 553 ms low, which is most of the field spread. the mean of the best three is not
BENCHMARK_TRIM = 3
HUBER_TUNING = 1.345
# rank is exact here, not a judgement call: a confounded design has a singular value at 1e-15
RANK_TOLERANCE = 1e-8

COLUMNS = (
    "season",
    "round",
    "session",
    "driver_code",
    "lap",
    "lap_time_millis",
    "stint",
    "lap_in_stint",
    "compound",
    "is_deleted",
    "is_accurate",
    "track_status",
    "pit_in",
    "pit_out",
    "position",
)


class Reason(StrEnum):
    NO_LAP_TIME = "no_lap_time"
    WET_COMPOUND = "wet_compound"
    NO_COMPOUND = "no_compound"
    DELETED = "deleted"
    OUT_LAP = "out_lap"
    IN_LAP = "in_lap"
    OPENING_LAPS = "opening_laps"
    TRACK_NOT_GREEN = "track_not_green"
    INACCURATE = "inaccurate"
    GAP_UNKNOWN = "gap_unknown"
    TRAFFIC = "traffic"


def is_green(status: pl.Expr) -> pl.Expr:
    """fastf1 concatenates one digit per marshal sector, so only an all-ones lap ran green."""
    return status.is_not_null() & status.str.replace_all("1", "").eq("")


def with_elapsed(laps: pl.DataFrame) -> pl.DataFrame:
    ordered = laps.sort(["driver_code", "lap"])
    return ordered.with_columns(
        pl.col("lap_time_millis").cum_sum().over("driver_code").alias("elapsed_millis"),
        # one missing lap time and every elapsed after it is wrong, not just unknown
        (pl.col("lap_time_millis").is_null().cum_sum().over("driver_code") == 0).alias(
            "elapsed_is_sound"
        ),
    )


def with_gap_ahead(laps: pl.DataFrame) -> pl.DataFrame:
    framed = with_elapsed(laps)
    ahead = (
        framed.select("lap", "position", "elapsed_millis", "elapsed_is_sound")
        .with_columns(pl.col("position") + 1)
        .rename(
            {"elapsed_millis": "ahead_elapsed_millis", "elapsed_is_sound": "ahead_elapsed_is_sound"}
        )
    )
    joined = framed.join(ahead, on=["lap", "position"], how="left")
    gapped = joined.with_columns(
        pl.when(pl.col("elapsed_is_sound") & pl.col("ahead_elapsed_is_sound"))
        .then(pl.col("elapsed_millis") - pl.col("ahead_elapsed_millis"))
        .otherwise(None)
        .alias("gap_ahead_millis")
    )
    # the gap at the line says nothing about the lap just driven, so carry the previous one too
    return gapped.sort("driver_code", "lap").with_columns(
        pl.col("gap_ahead_millis").shift(1).over("driver_code").alias("entry_gap_millis")
    )


def with_laps_remaining(laps: pl.DataFrame) -> pl.DataFrame:
    return laps.with_columns(
        (pl.col("lap").max().over("season", "round", "session") - pl.col("lap")).alias(
            "laps_remaining"
        )
    )


def classify(
    laps: pl.DataFrame, traffic_threshold_millis: int = TRAFFIC_THRESHOLD_MILLIS
) -> pl.DataFrame:
    """One reason per lap, first match wins, so the counts add up to the laps we started with."""
    framed = with_laps_remaining(with_gap_ahead(laps))
    leading = pl.col("position").is_not_null() & (pl.col("position") == 1)
    return framed.with_columns(
        pl.when(pl.col("lap_time_millis").is_null())
        .then(pl.lit(Reason.NO_LAP_TIME))
        .when(pl.col("compound").is_null())
        .then(pl.lit(Reason.NO_COMPOUND))
        .when(pl.col("compound").is_in(list(WET_COMPOUNDS)))
        .then(pl.lit(Reason.WET_COMPOUND))
        .when(pl.col("is_deleted"))
        .then(pl.lit(Reason.DELETED))
        .when(pl.col("pit_out") | (pl.col("lap_in_stint") == 1))
        .then(pl.lit(Reason.OUT_LAP))
        .when(pl.col("pit_in"))
        .then(pl.lit(Reason.IN_LAP))
        .when(pl.col("lap") <= OPENING_LAPS)
        .then(pl.lit(Reason.OPENING_LAPS))
        .when(~is_green(pl.col("track_status")))
        .then(pl.lit(Reason.TRACK_NOT_GREEN))
        .when(~pl.col("is_accurate"))
        .then(pl.lit(Reason.INACCURATE))
        .when(leading)
        .then(None)
        .when(pl.col("gap_ahead_millis").is_null())
        .then(pl.lit(Reason.GAP_UNKNOWN))
        .when(
            (pl.col("gap_ahead_millis") < traffic_threshold_millis)
            | (pl.col("entry_gap_millis") < traffic_threshold_millis)
        )
        .then(pl.lit(Reason.TRAFFIC))
        .otherwise(None)
        .alias("exclusion")
    )


def clean(classified: pl.DataFrame) -> pl.DataFrame:
    return classified.filter(pl.col("exclusion").is_null())


def exclusion_counts(classified: pl.DataFrame) -> dict[str, int]:
    """Published as a diagnostic: dropping most of the field silently is the failure mode."""
    counted = (
        classified.group_by("exclusion")
        .len()
        .filter(pl.col("exclusion").is_not_null())
        .sort("exclusion")
    )
    return {str(row["exclusion"]): int(row["len"]) for row in counted.to_dicts()}


def exclusion_rate(classified: pl.DataFrame) -> float:
    if not classified.height:
        return 0.0
    return int(classified["exclusion"].is_not_null().sum()) / classified.height


class UnidentifiableFitError(RuntimeError):
    def __init__(self, rank: int, params: int) -> None:
        super().__init__(f"pace design is rank {rank} of {params}, refusing to pseudo-invert")


class DriverPace(BaseModel, frozen=True):
    driver_code: str
    clean_pace_millis: float
    standard_error_millis: float
    interval_low_millis: float
    interval_high_millis: float
    clean_laps: int
    mean_tyre_age: float
    mean_race_progress: float
    # negative for the two or three cars quicker than the trimmed benchmark, which is expected
    percent_off_benchmark: float


class SessionPace(BaseModel, frozen=True):
    season: int
    round: int
    session: SessionKind
    drivers: list[DriverPace]
    b_tyre_millis: float
    b_progress_millis: float
    compound_offsets_millis: dict[str, float]
    reference_compound: str
    benchmark_millis: float
    clean_laps: int
    total_laps: int
    exclusions: dict[str, int]
    exclusion_rate: float
    condition_number: float


def _reference_compound(frame: pl.DataFrame) -> str:
    counted = frame.group_by("compound").len().sort("len", descending=True)
    return str(counted["compound"][0])


def _weights(residual: np.ndarray) -> np.ndarray:
    spread = 1.4826 * float(np.median(np.abs(residual - np.median(residual))))
    if spread <= 0:
        return np.ones_like(residual)
    cut = HUBER_TUNING * spread
    return np.where(np.abs(residual) <= cut, 1.0, cut / np.maximum(np.abs(residual), 1e-9))


def _huber(x: np.ndarray, y: np.ndarray, rounds: int = 25) -> tuple[np.ndarray, np.ndarray]:
    beta = np.linalg.solve(x.T @ x, x.T @ y)
    weight = np.ones(len(y))
    for _ in range(rounds):
        weight = _weights(y - x @ beta)
        scaled = x * weight[:, None]
        beta = np.linalg.solve(x.T @ scaled, scaled.T @ y)
    return beta, weight


def fit_session(
    laps: pl.DataFrame,
    traffic_threshold_millis: int = TRAFFIC_THRESHOLD_MILLIS,
    min_clean_laps: int = MIN_CLEAN_LAPS,
) -> SessionPace | None:
    classified = classify(laps, traffic_threshold_millis)
    kept = clean(classified)
    counts = kept.group_by("driver_code").len()
    enough = counts.filter(pl.col("len") >= min_clean_laps)["driver_code"].to_list()
    kept = kept.filter(pl.col("driver_code").is_in(enough))
    if kept.height < min_clean_laps or len(enough) < MIN_DRIVERS:
        return None

    drivers = sorted(enough)
    reference = _reference_compound(kept)
    others = sorted({str(c) for c in kept["compound"].to_list()} - {reference})
    tyre = kept["lap_in_stint"].to_numpy().astype(float)
    progress = kept["laps_remaining"].to_numpy().astype(float)
    # centred so the intercept sits at the design centroid, where its error is a quarter of
    # what it is out at the reference state, then shifted back analytically below
    tyre_mid, progress_mid = float(tyre.mean()), float(progress.mean())

    n = kept.height
    blocks = [np.zeros((n, len(drivers)))]
    code = kept["driver_code"].to_list()
    for row, name in enumerate(code):
        blocks[0][row, drivers.index(name)] = 1.0
    blocks.append((tyre - tyre_mid).reshape(-1, 1))
    blocks.append((progress - progress_mid).reshape(-1, 1))
    if others:
        dummies = np.zeros((n, len(others)))
        for row, name in enumerate(kept["compound"].to_list()):
            if str(name) in others:
                dummies[row, others.index(str(name))] = 1.0
        blocks.append(dummies)
    x = np.hstack(blocks)

    rank = int(np.linalg.matrix_rank(x, tol=RANK_TOLERANCE))
    if rank < x.shape[1]:
        raise UnidentifiableFitError(rank, x.shape[1])

    y = kept["lap_time_millis"].to_numpy().astype(float)
    beta, weight = _huber(x, y)
    residual = y - x @ beta
    dof = max(n - x.shape[1], 1)
    sigma2 = float(weight @ (residual**2)) / dof
    covariance = sigma2 * np.linalg.inv(x.T @ (x * weight[:, None]))
    critical = float(stats.t.ppf(0.975, dof))

    b_tyre = float(beta[len(drivers)])
    b_progress = float(beta[len(drivers) + 1])
    shift_tyre = REFERENCE_TYRE_AGE - tyre_mid
    shift_progress = REFERENCE_LAPS_REMAINING - progress_mid

    raw: list[tuple[str, float, float]] = []
    for index, name in enumerate(drivers):
        contrast = np.zeros(x.shape[1])
        contrast[index] = 1.0
        contrast[len(drivers)] = shift_tyre
        contrast[len(drivers) + 1] = shift_progress
        raw.append((name, float(contrast @ beta), float(np.sqrt(contrast @ covariance @ contrast))))

    benchmark = float(np.sort([pace for _, pace, _ in raw])[:BENCHMARK_TRIM].mean())
    paced: list[DriverPace] = []
    for name, pace, error in raw:
        own = kept.filter(pl.col("driver_code") == name)
        paced.append(
            DriverPace(
                driver_code=name,
                clean_pace_millis=pace,
                standard_error_millis=error,
                interval_low_millis=pace - critical * error,
                interval_high_millis=pace + critical * error,
                clean_laps=own.height,
                mean_tyre_age=float(own["lap_in_stint"].to_numpy().astype(float).mean()),
                mean_race_progress=float(own["laps_remaining"].to_numpy().astype(float).mean()),
                percent_off_benchmark=100.0 * (pace - benchmark) / benchmark,
            )
        )

    first = kept.row(0, named=True)
    return SessionPace(
        season=int(first["season"]),
        round=int(first["round"]),
        session=SessionKind(first["session"]),
        drivers=paced,
        b_tyre_millis=b_tyre,
        b_progress_millis=b_progress,
        compound_offsets_millis={
            name: float(beta[len(drivers) + 2 + i]) for i, name in enumerate(others)
        },
        reference_compound=reference,
        benchmark_millis=benchmark,
        clean_laps=kept.height,
        total_laps=classified.height,
        exclusions=exclusion_counts(classified),
        exclusion_rate=exclusion_rate(classified),
        condition_number=float(np.linalg.cond(x)),
    )
