# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from datetime import date

import numpy as np
import numpy.typing as npt
import polars as pl
from pydantic import BaseModel

HALF_LIFE_EVENTS = 20.0
MAX_STOPS = 3
# a circuit brings a handful of races, so every number here is shrunk toward the field with
# a pseudo-count worth about two race weekends of evidence
PRIOR_EVENTS = 2.0
PRIOR_STOPS = 20.0
# the window a stop can fall in. outside it a car is either serving a penalty or reacting to
# a safety car, and neither is a tyre decision
MIN_FRACTION = 0.08
MAX_FRACTION = 0.92
COLUMNS = (
    "season",
    "round",
    "race_date",
    "circuit_id",
    "driver_code",
    "stop",
    "fraction",
    "excess_millis",
    "starters",
)
DEGRADATION_COLUMNS = ("season", "round", "race_date", "circuit_id", "millis_per_lap")
SLICKS = ("SOFT", "MEDIUM", "HARD")
LETTERS = {name[0]: index for index, name in enumerate(SLICKS)}
STRATEGY_COLUMNS = ("season", "round", "race_date", "circuit_id", "driver_code", "grid", "sequence")
STINT_COLUMNS = (
    "season",
    "round",
    "race_date",
    "circuit_id",
    "driver_code",
    "compound",
    "laps",
    "wear_millis",
)
# the top ten choose their start tyre on saturday, the rest start on whatever they like
FRONT_ROWS = 10
# about one race of the circuit's own cars before its strategy mix moves off the field's
PRIOR_STRATEGY_CARS = 20.0
# a sequence the field almost never runs is noise, and every one kept is a column per path
MIN_STRATEGY_SHARE = 0.005
# a car this close to its planned stop takes the cheap one under a safety car instead
CAUTION_WINDOW = 8


class Strategy(BaseModel, frozen=True):
    """A compound sequence and how often a car starting in or out of the top ten ran it."""

    sequence: str
    front: float
    back: float


class TyreModel(BaseModel, frozen=True):
    as_of: date
    circuit_id: str
    half_life_events: float
    # what one more lap on the same set costs, in milliseconds
    degradation_millis: float
    field_degradation_millis: float
    # in-lap plus out-lap, measured against the same driver's own green pace
    pit_loss_millis: float
    field_pit_loss_millis: float
    # under a safety car the field is already crawling, so a stop costs a fraction of that
    safety_car_discount: float
    stop_counts: list[float]
    stop_spread: float
    events_used: int
    weighted_events: float
    # empty means no compound model: the wet scenarios, and any lake without stint data
    strategies: list[Strategy] = []
    # each compound's wear against the all-compound average the degradation figure carries
    wear_ratio: dict[str, float] = {}
    # relative stint length per compound, which is where a plan puts its stops
    stint_weights: dict[str, float] = {}


def decay(events_ago: np.ndarray, half_life: float = HALF_LIFE_EVENTS) -> np.ndarray:
    return np.power(0.5, events_ago / half_life)


def _weighted(frame: pl.DataFrame, half_life: float) -> tuple[pl.DataFrame, np.ndarray]:
    events = frame.select("season", "round", "race_date").unique().sort("race_date")
    ranked = events.with_columns(
        (pl.len() - pl.col("race_date").rank("dense").cast(pl.Int64)).alias("events_ago")
    )
    joined = frame.join(ranked, on=["season", "round", "race_date"])
    return joined, decay(joined["events_ago"].to_numpy().astype(float), half_life)


def _median(value: np.ndarray, weight: np.ndarray) -> float:
    """A pit stop under a red flag lands in the same column as one under green and is four
    times the number, so the middle of the distribution is the only statistic that survives."""
    if not value.size:
        return 0.0
    order = np.argsort(value)
    ranked: npt.NDArray[np.float64] = np.asarray(value, dtype=np.float64)[order]
    cumulative: npt.NDArray[np.float64] = np.cumsum(np.asarray(weight, dtype=np.float64)[order])
    total = float(cumulative[-1])
    if total <= 0.0:
        return float(np.median(ranked))
    return float(ranked[int(np.searchsorted(cumulative, total / 2.0))])


def _shrunk(here: np.ndarray, weight: np.ndarray, field: float, prior: float) -> float:
    if not here.size:
        return field
    mass = float(weight.sum())
    return float((_median(here, weight) * mass + field * prior) / (mass + prior))


SAFETY_CAR_DISCOUNT = 0.45


def fit(
    stops: pl.DataFrame,
    degradation: pl.DataFrame,
    circuit_id: str,
    as_of: date,
    half_life: float = HALF_LIFE_EVENTS,
    strategies: pl.DataFrame | None = None,
    stints: pl.DataFrame | None = None,
) -> TyreModel:
    deg_history = degradation.filter(pl.col("race_date") < as_of).drop_nulls("millis_per_lap")
    field_deg, here_deg = 0.0, 0.0
    if deg_history.height:
        joined, weight = _weighted(deg_history, half_life)
        value = joined["millis_per_lap"].to_numpy().astype(float)
        here = (joined["circuit_id"] == circuit_id).to_numpy()
        field_deg = _median(value, weight)
        here_deg = _shrunk(value[here], weight[here], field_deg, PRIOR_EVENTS)

    history = stops.filter(pl.col("race_date") < as_of)
    field_loss, here_loss, counts, spread, weighted, events = (
        0.0,
        0.0,
        [0.0, 1.0, 0.0, 0.0],
        0.08,
        0.0,
        0,
    )
    if history.height:
        joined, weight = _weighted(history, half_life)
        here = (joined["circuit_id"] == circuit_id).to_numpy()
        loss = joined["excess_millis"].to_numpy().astype(float)
        known = np.isfinite(loss)
        field_loss = _median(loss[known], weight[known])
        both = here & known
        here_loss = _shrunk(loss[both], weight[both], field_loss, PRIOR_EVENTS)
        counts = _stop_counts(joined, weight, here)
        spread = _spread(joined, weight, here)
        weighted = float(weight[here].sum())
        events = history.select("season", "round").unique().height
    return TyreModel(
        as_of=as_of,
        circuit_id=circuit_id,
        half_life_events=half_life,
        degradation_millis=here_deg,
        field_degradation_millis=field_deg,
        pit_loss_millis=here_loss,
        field_pit_loss_millis=field_loss,
        safety_car_discount=SAFETY_CAR_DISCOUNT,
        stop_counts=counts,
        stop_spread=spread,
        events_used=events,
        weighted_events=weighted,
        strategies=(
            fit_strategies(strategies, circuit_id, as_of, half_life)
            if strategies is not None
            else []
        ),
        wear_ratio=_wear_ratio(stints, as_of, half_life) if stints is not None else {},
        stint_weights=(
            _per_compound(stints, "laps", circuit_id, as_of, half_life)
            if stints is not None
            else {}
        ),
    )


def fit_strategies(
    frame: pl.DataFrame, circuit_id: str, as_of: date, half_life: float = HALF_LIFE_EVENTS
) -> list[Strategy]:
    """Two compounds are compulsory in the dry, so a one-compound sequence is a car that
    retired before its stop and says nothing about the plan it was on."""
    distinct = sum(pl.col("sequence").str.contains(letter).cast(pl.Int64) for letter in LETTERS)
    history = frame.filter(
        (pl.col("race_date") < as_of)
        & pl.col("sequence").str.contains(r"^[SMH]{2,4}$")
        & (distinct > 1)
    )
    if not history.height:
        return []
    joined, weight = _weighted(history, half_life)
    here = (joined["circuit_id"] == circuit_id).to_numpy()
    front = (joined["grid"].fill_null(99) <= FRONT_ROWS).to_numpy()
    names = joined["sequence"].to_list()
    known = sorted(set(names))
    index = {name: slot for slot, name in enumerate(known)}
    column = np.array([index[name] for name in names])

    def share(mask: np.ndarray) -> np.ndarray:
        counts = np.bincount(column[mask], weights=weight[mask], minlength=len(known))
        return counts

    out: dict[str, np.ndarray] = {}
    for label, half in (("front", front), ("back", ~front)):
        field = share(half)
        field = field / field.sum() if field.sum() else np.full(len(known), 1.0 / len(known))
        mine = share(half & here)
        mixed = (mine + PRIOR_STRATEGY_CARS * field) / (mine.sum() + PRIOR_STRATEGY_CARS)
        mixed = np.where(mixed >= MIN_STRATEGY_SHARE, mixed, 0.0)
        out[label] = mixed / mixed.sum()
    return [
        Strategy(sequence=name, front=float(out["front"][slot]), back=float(out["back"][slot]))
        for slot, name in enumerate(known)
        if out["front"][slot] > 0.0 or out["back"][slot] > 0.0
    ]


def _per_compound(
    frame: pl.DataFrame,
    column: str,
    circuit_id: str,
    as_of: date,
    half_life: float,
) -> dict[str, float]:
    history = frame.filter(
        (pl.col("race_date") < as_of) & pl.col("compound").is_in(list(SLICKS))
    ).drop_nulls(column)
    if not history.height:
        return {}
    joined, weight = _weighted(history, half_life)
    here = (joined["circuit_id"] == circuit_id).to_numpy()
    out: dict[str, float] = {}
    for name in SLICKS:
        mine = (joined["compound"] == name).to_numpy()
        if not mine.any():
            continue
        value = joined[column].to_numpy().astype(float)
        field = _median(value[mine], weight[mine])
        out[name] = _shrunk(value[mine & here], weight[mine & here], field, PRIOR_EVENTS)
    return out


def _wear_ratio(frame: pl.DataFrame, as_of: date, half_life: float) -> dict[str, float]:
    """Field-wide on purpose: one circuit's few stints per compound cannot say whether its
    softs fall away twice as fast as its hards, the whole calendar can."""
    history = frame.filter(
        (pl.col("race_date") < as_of) & pl.col("compound").is_in(list(SLICKS))
    ).drop_nulls("wear_millis")
    if not history.height:
        return {}
    joined, weight = _weighted(history, half_life)
    value = joined["wear_millis"].to_numpy().astype(float)
    overall = _median(value, weight)
    if overall <= 0.0:
        return {}
    out: dict[str, float] = {}
    for name in SLICKS:
        mine = (joined["compound"] == name).to_numpy()
        if mine.any():
            out[name] = max(_median(value[mine], weight[mine]) / overall, 0.0)
    return out


def sample_plan(
    model: TyreModel,
    grid: np.ndarray,
    paths: int,
    drivers: int,
    laps: int,
    rng: np.random.Generator,
) -> tuple[npt.NDArray[np.bool_], npt.NDArray[np.int64]]:
    """(paths, drivers, laps) of stops and (paths, drivers, MAX_STOPS + 1) of the compound each
    stint runs on. Stops split the race in proportion to how long each compound usually lasts."""
    slots = np.broadcast_to(np.asarray(grid, dtype=int), (paths, drivers))
    sequences = [item.sequence for item in model.strategies]
    chosen = np.empty((paths, drivers), dtype=np.int64)
    for label, mask in (("front", slots <= FRONT_ROWS), ("back", slots > FRONT_ROWS)):
        weights = np.asarray([getattr(item, label) for item in model.strategies])
        if not mask.any() or weights.sum() <= 0.0:
            weights = np.ones(len(sequences))
        chosen[mask] = rng.choice(len(sequences), size=int(mask.sum()), p=weights / weights.sum())

    stints = MAX_STOPS + 1
    table = np.zeros((len(sequences), stints), dtype=np.int64)
    span = np.zeros((len(sequences), stints))
    count = np.zeros(len(sequences), dtype=np.int64)
    for row, sequence in enumerate(sequences):
        codes = [LETTERS[letter] for letter in sequence[:stints]]
        count[row] = len(codes)
        table[row, : len(codes)] = codes
        table[row, len(codes) :] = codes[-1]
        for slot, code in enumerate(codes):
            span[row, slot] = model.stint_weights.get(SLICKS[code], 1.0)

    plan = table[chosen]
    planned = count[chosen] - 1
    share = span[chosen]
    total = share.sum(axis=2, keepdims=True)
    boundary = np.cumsum(share, axis=2) / np.where(total > 0.0, total, 1.0)
    pitting = np.zeros((paths, drivers, laps), dtype=bool)
    previous = np.zeros((paths, drivers), dtype=int)
    for stop in range(MAX_STOPS):
        doing = planned > stop
        if not doing.any():
            continue
        fraction = np.clip(
            boundary[:, :, stop] + rng.normal(0.0, model.stop_spread, (paths, drivers)),
            MIN_FRACTION,
            MAX_FRACTION,
        )
        lap = np.clip(np.rint(fraction * laps).astype(int), previous + 1, laps - 1)
        rows, cars = np.nonzero(doing & (lap < laps - 1))
        pitting[rows, cars, lap[rows, cars]] = True
        previous = np.where(doing, lap, previous)
    return pitting, plan


def pit_under_caution(
    pitting: npt.NDArray[np.bool_],
    under: npt.NDArray[np.bool_],
    lap: int,
    window: int = CAUTION_WINDOW,
) -> npt.NDArray[np.bool_]:
    """A safety car halves what a stop costs, so a car whose stop was due within a few laps
    comes in now and the stop it had planned is the one it no longer makes."""
    ahead = pitting[:, :, lap + 1 : lap + 1 + window]
    if not ahead.size:
        return pitting
    moving = ahead.any(axis=2) & under.reshape(-1, 1) & ~pitting[:, :, lap]
    if not moving.any():
        return pitting
    first = ahead.argmax(axis=2) + lap + 1
    rows, cars = np.nonzero(moving)
    pitting[rows, cars, first[rows, cars]] = False
    pitting[rows, cars, lap] = True
    return pitting


def _stop_counts(joined: pl.DataFrame, weight: np.ndarray, here: np.ndarray) -> list[float]:
    """A car that never pitted leaves no row, so zero-stoppers are counted as the starters
    the stop rows do not account for."""
    counts = np.zeros(MAX_STOPS + 1)
    framed = joined.with_columns(pl.Series("weight", weight), pl.Series("here", here))
    scoped = framed.filter(pl.col("here"))
    if not scoped.height:
        return [0.0, 1.0, 0.0, 0.0]
    per_driver = scoped.group_by("season", "round", "driver_code").agg(
        pl.col("stop").max().alias("stops"), pl.col("weight").first().alias("weight")
    )
    for row in per_driver.iter_rows(named=True):
        counts[min(int(row["stops"]), MAX_STOPS)] += float(row["weight"])
    per_race = scoped.group_by("season", "round").agg(
        pl.col("starters").first().alias("starters"),
        pl.col("driver_code").n_unique().alias("stopped"),
        pl.col("weight").first().alias("weight"),
    )
    for row in per_race.iter_rows(named=True):
        counts[0] += float(row["weight"]) * max(int(row["starters"]) - int(row["stopped"]), 0)
    smoothed = counts + PRIOR_STOPS * np.array([0.02, 0.5, 0.42, 0.06])
    return [float(value) for value in smoothed / smoothed.sum()]


def _spread(joined: pl.DataFrame, weight: np.ndarray, here: np.ndarray) -> float:
    """How far a stop drifts from the lap an even split would put it on."""
    framed = joined.with_columns(pl.Series("weight", weight), pl.Series("here", here)).filter(
        pl.col("here")
    )
    if not framed.height:
        return 0.08
    per_driver = framed.group_by("season", "round", "driver_code").agg(
        pl.col("stop").max().alias("stops")
    )
    with_total = framed.join(per_driver, on=["season", "round", "driver_code"])
    fraction = with_total["fraction"].to_numpy().astype(float)
    even = with_total["stop"].to_numpy().astype(float) / (
        with_total["stops"].to_numpy().astype(float) + 1.0
    )
    mass = with_total["weight"].to_numpy().astype(float)
    variance = float((mass * (fraction - even) ** 2).sum() / max(mass.sum(), 1e-9))
    return float(max(np.sqrt(variance), 0.02))


def sample_stops(
    model: TyreModel, paths: int, drivers: int, laps: int, rng: np.random.Generator
) -> npt.NDArray[np.bool_]:
    """(paths, drivers, laps) of whether that car comes in at the end of that lap."""
    pitting = np.zeros((paths, drivers, laps), dtype=bool)
    counts = np.asarray(model.stop_counts)
    planned = rng.choice(len(counts), size=(paths, drivers), p=counts / counts.sum())
    for stop in range(1, MAX_STOPS + 1):
        doing = planned >= stop
        if not doing.any():
            continue
        even = stop / (planned + 1.0)
        fraction = np.clip(
            even + rng.normal(0.0, model.stop_spread, (paths, drivers)),
            MIN_FRACTION,
            MAX_FRACTION,
        )
        lap = np.clip(np.rint(fraction * laps).astype(int), 1, laps - 1)
        rows, cars = np.nonzero(doing)
        pitting[rows, cars, lap[rows, cars]] = True
    return pitting
