# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date
from pathlib import Path
from typing import Any, Literal

import numpy as np
import polars as pl
import yaml
from pydantic import BaseModel, Field

RELATIVE = Path("data/reference/circuits.yml")


def _taxonomy() -> Path:
    """The file is hand-maintained at the repo root, not shipped inside the package, so it
    is found by walking up from here rather than by trusting the working directory."""
    for parent in Path(__file__).resolve().parents:
        found = parent / RELATIVE
        if found.exists():
            return found
    return RELATIVE


TAXONOMY = _taxonomy()
DEMANDS = ("downforce", "traction", "braking", "top_speed", "abrasion", "kerbs")
FEATURES = ("length_km", "corners", "altitude_m", *DEMANDS)


class Demand(BaseModel, frozen=True):
    downforce: int = Field(ge=1, le=5)
    traction: int = Field(ge=1, le=5)
    braking: int = Field(ge=1, le=5)
    top_speed: int = Field(ge=1, le=5)
    abrasion: int = Field(ge=1, le=5)
    kerbs: int = Field(ge=1, le=5)


class CircuitProfile(BaseModel, frozen=True):
    circuit_id: str
    length_km: float = Field(gt=2.0, lt=8.0)
    corners: int = Field(ge=8, le=30)
    direction: Literal["clockwise", "anticlockwise"]
    altitude_m: int = Field(ge=-50, le=3000)
    # the first season of the current layout, when it changed inside our window
    reprofiled: int | None = None
    demand: Demand


def load(path: Path = TAXONOMY) -> dict[str, CircuitProfile]:
    rows: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())
    return {
        circuit_id: CircuitProfile(circuit_id=circuit_id, **row) for circuit_id, row in rows.items()
    }


def vector(profile: CircuitProfile) -> np.ndarray:
    demand = profile.demand
    return np.array(
        [
            profile.length_km,
            profile.corners,
            profile.altitude_m,
            *[getattr(demand, d) for d in DEMANDS],
        ],
        dtype=float,
    )


def matrix(profiles: dict[str, CircuitProfile]) -> tuple[list[str], np.ndarray]:
    """Z-scored, because altitude spans 2240 m and a demand band spans 4."""
    ids = sorted(profiles)
    raw = np.vstack([vector(profiles[circuit_id]) for circuit_id in ids])
    spread = raw.std(axis=0)
    spread[spread == 0] = 1.0
    return ids, (raw - raw.mean(axis=0)) / spread


def comparable_from(profile: CircuitProfile) -> int | None:
    return profile.reprofiled


HALF_LIFE_EVENTS = 20.0
# nine z-scored features against a season or two of races per team. mild shrinkage, enough to
# keep two collinear demand bands from trading a huge coefficient each
RIDGE = 1.0
NEIGHBOURS = 5
CONFIDENCE_Z = 1.96

COLUMNS = ("season", "round", "race_date", "circuit_id", "constructor_id", "value")


class UnknownCircuitError(KeyError):
    def __init__(self, circuit_id: str) -> None:
        super().__init__(f"{circuit_id} is not in the circuit taxonomy")


class Prediction(BaseModel, frozen=True):
    constructor_id: str
    estimate: float
    # error on the estimate, which is what the two estimators are compared on
    standard_error: float
    interval_low: float
    interval_high: float
    # race-to-race dispersion around the estimate. a forecast wants both of these added in
    # quadrature; asking whether the estimators disagree wants only the one above
    spread: float
    samples: int
    effective_samples: float


class Neighbour(BaseModel, frozen=True):
    circuit_id: str
    similarity: float


class TrackFit(BaseModel, frozen=True):
    as_of: date
    circuit_id: str
    half_life_events: float
    ridge: float
    regression: list[Prediction]
    similarity: list[Prediction]
    neighbours: list[Neighbour]
    # the two estimators are shown side by side, never blended. a constructor lands here when
    # their intervals do not overlap, which is a finding and not an error
    disagreements: list[str]
    events_used: int
    events_dropped: int
    dropped_reprofiled: int


def cosine(target: np.ndarray, others: np.ndarray) -> np.ndarray:
    scale = np.linalg.norm(others, axis=1) * np.linalg.norm(target)
    scale[scale == 0] = 1.0
    return (others @ target) / scale


def nearest(
    circuit_id: str,
    profiles: dict[str, CircuitProfile],
    k: int = NEIGHBOURS,
) -> list[Neighbour]:
    """The circuit itself is its own nearest neighbour: a team's record here is the strongest
    evidence about here, and dropping it to look only at lookalikes throws that away."""
    if circuit_id not in profiles:
        raise UnknownCircuitError(circuit_id)
    ids, scaled = matrix(profiles)
    target = scaled[ids.index(circuit_id)]
    scores = cosine(target, scaled)
    ranked = sorted(zip(ids, scores, strict=True), key=lambda item: -item[1])
    return [Neighbour(circuit_id=name, similarity=float(score)) for name, score in ranked[: k + 1]]


def _decay(events_ago: np.ndarray, half_life: float) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _aged(frame: pl.DataFrame, as_of: date, half_life: float) -> pl.DataFrame:
    history = frame.filter(pl.col("race_date") < as_of)
    events = history.select("race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = history.join(ranked, on="race_date")
    return joined.with_columns(
        pl.Series("weight", _decay(joined["events_ago"].to_numpy().astype(float), half_life))
    )


def _comparable(
    frame: pl.DataFrame, profiles: dict[str, CircuitProfile]
) -> tuple[pl.DataFrame, int]:
    """A reprofiled circuit is a different track wearing the same name, so its old races are
    not evidence about the current layout."""
    keep = pl.lit(True)
    for circuit_id, profile in profiles.items():
        since = comparable_from(profile)
        if since is None:
            continue
        keep = keep & ~((pl.col("circuit_id") == circuit_id) & (pl.col("season") < since))
    kept = frame.filter(keep)
    return kept, frame.height - kept.height


def _weighted_mean(value: np.ndarray, weight: np.ndarray) -> tuple[float, float, float]:
    total = float(weight.sum())
    mean = float(weight @ value / total)
    if len(value) < 2:
        return mean, float("inf"), 0.0
    squared = float((weight**2).sum())
    effective = total - squared / total
    variance = float(weight @ (value - mean) ** 2) / max(effective, 1e-9)
    error = float(np.sqrt(max(variance, 0.0) * squared) / total)
    return mean, error, float(np.sqrt(max(variance, 0.0)))


def by_similarity(
    frame: pl.DataFrame,
    neighbours: list[Neighbour],
    minimum: float = 0.0,
) -> list[Prediction]:
    close = {n.circuit_id: max(n.similarity, minimum) for n in neighbours if n.similarity > minimum}
    scored = frame.filter(pl.col("circuit_id").is_in(list(close))).with_columns(
        pl.col("circuit_id").replace_strict(close, return_dtype=pl.Float64).alias("similarity")
    )
    found: list[Prediction] = []
    for constructor_id, rows in sorted(scored.group_by("constructor_id")):
        weight = rows["weight"].to_numpy() * rows["similarity"].to_numpy()
        value = rows["value"].to_numpy().astype(float)
        if not weight.sum():
            continue
        mean, error, spread = _weighted_mean(value, weight)
        found.append(
            Prediction(
                constructor_id=str(constructor_id[0]),
                estimate=mean,
                standard_error=error,
                interval_low=mean - CONFIDENCE_Z * error,
                interval_high=mean + CONFIDENCE_Z * error,
                spread=spread,
                samples=rows.height,
                effective_samples=float(weight.sum()),
            )
        )
    return found


def by_regression(
    frame: pl.DataFrame,
    circuit_id: str,
    profiles: dict[str, CircuitProfile],
    ridge: float = RIDGE,
) -> list[Prediction]:
    ids, scaled = matrix(profiles)
    lookup = {name: scaled[position] for position, name in enumerate(ids)}
    target = np.concatenate([[1.0], lookup[circuit_id]])
    known = frame.filter(pl.col("circuit_id").is_in(ids))

    found: list[Prediction] = []
    for constructor_id, rows in sorted(known.group_by("constructor_id")):
        design = np.vstack(
            [np.concatenate([[1.0], lookup[str(name)]]) for name in rows["circuit_id"]]
        )
        value = rows["value"].to_numpy().astype(float)
        weight = rows["weight"].to_numpy()
        # the intercept carries the team's level and must not be shrunk towards zero pace
        penalty = ridge * np.eye(design.shape[1])
        penalty[0, 0] = 0.0
        weighted = design * weight[:, None]
        inverse = np.linalg.inv(design.T @ weighted + penalty)
        beta = inverse @ (weighted.T @ value)

        residual = value - design @ beta
        fitted = float(np.trace(inverse @ (design.T @ weighted)))
        dof = max(float(weight.sum()) - fitted, 1.0)
        variance = float(weight @ (residual**2)) / dof
        squared = design * (weight**2)[:, None]
        covariance = variance * (inverse @ (design.T @ squared) @ inverse)
        error = float(np.sqrt(max(float(target @ covariance @ target), 0.0)))
        estimate = float(target @ beta)
        found.append(
            Prediction(
                constructor_id=str(constructor_id[0]),
                estimate=estimate,
                standard_error=error,
                interval_low=estimate - CONFIDENCE_Z * error,
                interval_high=estimate + CONFIDENCE_Z * error,
                spread=float(np.sqrt(max(variance, 0.0))),
                samples=rows.height,
                effective_samples=float(weight.sum()),
            )
        )
    return found


def _overlap(a: Prediction, b: Prediction) -> bool:
    return a.interval_low <= b.interval_high and b.interval_low <= a.interval_high


def fit(
    frame: pl.DataFrame,
    circuit_id: str,
    as_of: date,
    profiles: dict[str, CircuitProfile] | None = None,
    half_life: float = HALF_LIFE_EVENTS,
    ridge: float = RIDGE,
    k: int = NEIGHBOURS,
) -> TrackFit:
    known = profiles or load()
    if circuit_id not in known:
        raise UnknownCircuitError(circuit_id)
    aged = _aged(frame, as_of, half_life)
    comparable, reprofiled = _comparable(aged, known)
    close = nearest(circuit_id, known, k)

    regression = by_regression(comparable, circuit_id, known, ridge)
    similarity = by_similarity(comparable, close)
    paired = {item.constructor_id: item for item in similarity}
    return TrackFit(
        as_of=as_of,
        circuit_id=circuit_id,
        half_life_events=half_life,
        ridge=ridge,
        regression=regression,
        similarity=similarity,
        neighbours=close,
        disagreements=sorted(
            item.constructor_id
            for item in regression
            if item.constructor_id in paired and not _overlap(item, paired[item.constructor_id])
        ),
        events_used=comparable.select("race_date").n_unique(),
        events_dropped=frame.height - aged.height,
        dropped_reprofiled=reprofiled,
    )
