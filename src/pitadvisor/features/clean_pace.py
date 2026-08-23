# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from enum import StrEnum

import polars as pl

OPENING_LAPS = 2
# a car this close is running in the dirty air of the one ahead, so its lap time is a
# measurement of the gap and not of its own pace
TRAFFIC_THRESHOLD_MILLIS = 1_500
REFERENCE_TYRE_AGE = 1
REFERENCE_LAPS_REMAINING = 0

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
    return joined.with_columns(
        pl.when(pl.col("elapsed_is_sound") & pl.col("ahead_elapsed_is_sound"))
        .then(pl.col("elapsed_millis") - pl.col("ahead_elapsed_millis"))
        .otherwise(None)
        .alias("gap_ahead_millis")
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
        .when(pl.col("gap_ahead_millis") < traffic_threshold_millis)
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
