"""Pulls the 2026 sessions fastf1 already gave us from openf1 into a scratch lake, then compares
the two lap by lap and fit by fit. openf1 only replaces fastf1 once this says the two agree.

    uv run python scripts/openf1_overlap.py fetch <scratch dir>
    uv run python scripts/openf1_overlap.py compare <scratch dir> results/openf1_overlap.json
"""

import json
import sys
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from pitadvisor.config import get_settings
from pitadvisor.features import clean_pace
from pitadvisor.ingest import openf1
from pitadvisor.ingest.ratelimit import HOURLY_CAPS, LocalBucket, LocalLedger, RateLimiter
from pitadvisor.ingest.raw_store import LocalObjectStore, RawStore, object_store
from pitadvisor.quality.checks import read_table
from pitadvisor.types import Layer, SessionKind

SEASON = 2026
LAST_FASTF1_ROUND = 17
KINDS = (SessionKind.RACE, SessionKind.SPRINT)
KEYS = ["round", "session", "driver_code", "lap"]
COMPARED = (
    "compound",
    "stint",
    "lap_in_stint",
    "tyre_life",
    "pit_in",
    "pit_out",
    "is_deleted",
    "is_accurate",
    "position",
    "green",
)


def fetch(scratch: Path) -> None:
    lake = object_store(get_settings())
    races = read_table(lake, Layer.BRONZE, "races")
    assert races is not None
    held = {
        int(row["round"]): row["race_date"]
        for row in races.to_dicts()
        if row["season"] == SEASON and int(row["round"]) <= LAST_FASTF1_ROUND
    }
    store = LocalObjectStore(scratch)
    limiter = RateLimiter(
        LocalBucket(
            scratch / "quota.json",
            name="openf1",
            capacity=HOURLY_CAPS["openf1"],
            refill_per_second=HOURLY_CAPS["openf1"] / 3600,
        )
    )
    client = openf1.OpenF1Client(
        RawStore(store), LocalLedger(scratch / "ledger.json"), limiter, "overlap"
    )
    for outcome in openf1.backfill(client, store, SEASON, held, KINDS):
        print(outcome.round, outcome.rows, outcome.quarantined, outcome.skipped or "", flush=True)


def _laps(store: Any) -> pl.DataFrame:
    frame = read_table(store, Layer.BRONZE, "session_laps")
    assert frame is not None
    return frame.filter(
        (pl.col("season") == SEASON)
        & (pl.col("round") <= LAST_FASTF1_ROUND)
        & pl.col("session").is_in([str(kind) for kind in KINDS])
    ).with_columns(green=clean_pace.is_green(pl.col("track_status")))


def _spread(values: np.ndarray) -> dict[str, float]:
    if not len(values):
        return {}
    return {
        "median": float(np.median(values)),
        "p95": float(np.percentile(values, 95)),
        "max": float(values.max()),
    }


def _classified(laps: pl.DataFrame) -> pl.DataFrame:
    # classify reads one session: its gap-ahead join is on lap and position alone
    return pl.concat(
        [clean_pace.classify(frame) for _, frame in laps.group_by(["round", "session"])],
        how="diagonal_relaxed",
    )


def _fits(laps: pl.DataFrame) -> dict[tuple[int, str], clean_pace.SessionPace]:
    fitted: dict[tuple[int, str], clean_pace.SessionPace] = {}
    for (round_, session), frame in laps.group_by(["round", "session"]):
        try:
            found = clean_pace.fit_session(frame)
        except clean_pace.UnidentifiableFitError:
            found = None
        if found is not None:
            fitted[(int(round_), str(session))] = found
    return fitted


def compare(scratch: Path, output: Path) -> None:
    fastf1 = _laps(object_store(get_settings()))
    ours = _laps(LocalObjectStore(scratch))
    joined = fastf1.join(ours, on=KEYS, how="inner", suffix="_openf1")
    report: dict[str, Any] = {
        "season": SEASON,
        "rounds": sorted(set(fastf1["round"].to_list())),
        "rows": {
            "fastf1": fastf1.height,
            "openf1": ours.height,
            "matched": joined.height,
        },
        "sessions": {
            "fastf1": fastf1.select("round", "session").unique().height,
            "openf1": ours.select("round", "session").unique().height,
        },
    }
    timed = joined.drop_nulls(["lap_time_millis", "lap_time_millis_openf1"])
    report["lap_time_abs_diff_millis"] = _spread(
        (timed["lap_time_millis"] - timed["lap_time_millis_openf1"]).abs().to_numpy()
    )
    report["lap_time_within_1ms"] = float(
        ((timed["lap_time_millis"] - timed["lap_time_millis_openf1"]).abs() <= 1).mean() or 0
    )
    report["agreement"] = {
        column: float((joined[column].eq_missing(joined[f"{column}_openf1"])).mean() or 0)
        for column in COMPARED
    }
    fastf1_classified, openf1_classified = _classified(fastf1), _classified(ours)
    fastf1_classes = fastf1_classified.select(*KEYS, "exclusion")
    openf1_classes = openf1_classified.select(*KEYS, "exclusion")
    classes = fastf1_classes.join(openf1_classes, on=KEYS, suffix="_openf1")
    report["exclusion_agreement"] = float(
        classes["exclusion"].eq_missing(classes["exclusion_openf1"]).mean() or 0
    )
    report["exclusion_rate"] = {
        "fastf1": clean_pace.exclusion_rate(fastf1_classified),
        "openf1": clean_pace.exclusion_rate(openf1_classified),
    }
    report["exclusion_confusion"] = (
        classes.filter(~pl.col("exclusion").eq_missing(pl.col("exclusion_openf1")))
        .group_by("exclusion", "exclusion_openf1")
        .len()
        .sort("len", descending=True)
        .head(12)
        .to_dicts()
    )
    theirs, mine = _fits(fastf1), _fits(ours)
    pace_diffs: list[float] = []
    within_interval = 0
    tyre_diffs: list[float] = []
    for marker in sorted(set(theirs) & set(mine)):
        tyre_diffs.append(abs(theirs[marker].b_tyre_millis - mine[marker].b_tyre_millis))
        by_driver = {item.driver_code: item for item in mine[marker].drivers}
        for driver in theirs[marker].drivers:
            other = by_driver.get(driver.driver_code)
            if other is None:
                continue
            pace_diffs.append(abs(driver.clean_pace_millis - other.clean_pace_millis))
            within_interval += (
                driver.interval_low_millis <= other.clean_pace_millis <= driver.interval_high_millis
            )
    report["fits"] = {
        "fastf1": len(theirs),
        "openf1": len(mine),
        "both": len(set(theirs) & set(mine)),
        "driver_pace_abs_diff_millis": _spread(np.array(pace_diffs)),
        "driver_pace_inside_fastf1_interval": within_interval / len(pace_diffs)
        if pace_diffs
        else 0.0,
        "tyre_slope_abs_diff_millis": _spread(np.array(tyre_diffs)),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, default=str) + "\n")
    print(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    if sys.argv[1] == "fetch":
        fetch(Path(sys.argv[2]))
    else:
        compare(Path(sys.argv[2]), Path(sys.argv[3]))
