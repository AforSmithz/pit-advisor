# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import polars as pl

from pitadvisor.features.clean_pace import SLICKS, is_green
from pitadvisor.types import SessionKind

COLUMNS = ("season", "round", "race_date", "constructor_id", "offset")
SCHEMA: dict[str, type[pl.DataType]] = {
    "season": pl.Int64,
    "round": pl.Int64,
    "race_date": pl.Date,
    "constructor_id": pl.String,
    "offset": pl.Float64,
}
MIN_RUN_LAPS = 5
# a push lap or a cool-down inside a long run sits well outside this, a fuel-heavy lap does not
RUN_WINDOW = 1.07


def long_runs(laps: pl.DataFrame) -> pl.DataFrame:
    """One row per stint that was a long run, with its median lap. Practice has no running
    order to read traffic from, so a run is judged by its own laps only."""
    usable = laps.filter(
        pl.col("lap_time_millis").is_not_null()
        & pl.col("compound").is_in(list(SLICKS))
        & is_green(pl.col("track_status"))
        & ~pl.col("pit_in")
        & ~pl.col("pit_out")
        & ~pl.col("is_deleted")
        & pl.col("is_accurate")
        & (pl.col("lap_in_stint") >= 2)
    )
    keys = ["season", "round", "driver_code", "stint"]
    banded = usable.with_columns(
        pl.col("lap_time_millis").median().over(keys).alias("stint_median")
    ).filter(pl.col("lap_time_millis") <= pl.col("stint_median") * RUN_WINDOW)
    return (
        banded.group_by(keys)
        .agg(
            pl.col("lap_time_millis").median().alias("median_millis"),
            pl.col("compound").mode().first().alias("compound"),
            pl.len().alias("laps"),
        )
        .filter(pl.col("laps") >= MIN_RUN_LAPS)
    )


def offsets(
    laps: pl.DataFrame, results: pl.DataFrame, schedule: pl.DataFrame | None
) -> pl.DataFrame:
    """Each team's long-run pace against the field on the same compound, in percent, from FP2,
    or FP1 on a sprint weekend, which has no other practice before qualifying."""
    if not laps.height or schedule is None or "sprint_date" not in schedule.columns:
        return pl.DataFrame(schema=SCHEMA)
    weekends = schedule.select(
        "season", "round", "race_date", pl.col("sprint_date").is_not_null().alias("sprint")
    ).unique(subset=["season", "round"], keep="last")
    chosen = laps.join(weekends, on=["season", "round"], how="inner").filter(
        pl.when(pl.col("sprint"))
        .then(pl.col("session") == str(SessionKind.FP1))
        .otherwise(pl.col("session") == str(SessionKind.FP2))
    )
    runs = long_runs(chosen)
    if not runs.height:
        return pl.DataFrame(schema=SCHEMA)
    field = runs.group_by("season", "round", "compound").agg(
        pl.col("median_millis").median().alias("field_millis")
    )
    seats = results.select("season", "round", "driver_code", "constructor_id").drop_nulls()
    return (
        runs.join(field, on=["season", "round", "compound"])
        .with_columns(
            (100.0 * (pl.col("median_millis") / pl.col("field_millis") - 1.0)).alias("offset")
        )
        .join(seats, on=["season", "round", "driver_code"], how="inner")
        .group_by("season", "round", "constructor_id")
        .agg(pl.col("offset").mean())
        .join(weekends.select("season", "round", "race_date"), on=["season", "round"])
        .select(
            pl.col("season").cast(pl.Int64),
            pl.col("round").cast(pl.Int64),
            pl.col("race_date").cast(pl.Date),
            "constructor_id",
            pl.col("offset").cast(pl.Float64),
        )
    )
