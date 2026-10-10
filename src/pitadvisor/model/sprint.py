# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import dataclasses
from datetime import UTC, date, datetime
from typing import Literal

import numpy as np
import polars as pl

from pitadvisor.features import form, track_fit
from pitadvisor.features import grid as starting
from pitadvisor.features.assemble import EventContext
from pitadvisor.model import backtest, baselines
from pitadvisor.model import pool as pooling
from pitadvisor.model.metrics import log_loss
from pitadvisor.sim import race

# a sprint is about a third of the race distance, used where a circuit has never held one
SPRINT_SHARE = 1.0 / 3.0
COLUMNS = (
    "season",
    "round",
    "race_date",
    "driver_id",
    "driver_code",
    "constructor_id",
    "grid",
    "position",
    "points",
    "laps_completed",
)
WARMUP = 4
SprintState = Literal["no_sprint", "awaiting_grid", "forecast", "run"]
# a sprint pays the top eight
SPRINT_POINTS = 8


def sprint_frame(sprint: pl.DataFrame | None, schedule: pl.DataFrame | None) -> pl.DataFrame:
    """The jolpica sprint classification dated the day the sprint ran."""
    if sprint is None or schedule is None or "sprint_date" not in schedule.columns:
        return pl.DataFrame(schema={name: pl.Utf8 for name in COLUMNS})
    dated = (
        schedule.drop_nulls("sprint_date")
        .select("season", "round", pl.col("sprint_date").cast(pl.Date).alias("race_date"))
        .unique(subset=["season", "round"], keep="last")
    )
    return (
        sprint.join(dated, on=["season", "round"], how="inner")
        .with_columns(pl.col("points").cast(pl.Float64))
        .select(COLUMNS)
    )


def before_sprint(pane: backtest.Panel, context: EventContext) -> backtest.Panel:
    """The panel on the morning of the sprint: the weekend's race has not run, and from 2024
    main qualifying has not either."""
    held = (pl.col("season") == context.season) & (pl.col("round") == context.round)
    if context.season >= backtest.SPRINT_BEFORE_QUALI_FROM:
        return dataclasses.replace(
            backtest.before_qualifying(pane, context), sprint_pace=pane.sprint_pace
        )
    return dataclasses.replace(pane, results=pane.results.filter(~held))


def sprint_laps(sprints: pl.DataFrame, pane: backtest.Panel, context: EventContext) -> int:
    earlier = sprints.filter(pl.col("race_date") < context.race_date)
    here = earlier.join(
        pane.events.select("season", "round", "circuit_id"), on=["season", "round"]
    ).filter(pl.col("circuit_id") == context.circuit_id)
    if here.height:
        return int(str(here["laps_completed"].max()))
    return max(round(backtest.race_laps(pane, context, context.race_date) * SPRINT_SHARE), 1)


def scenarios(
    pane: backtest.Panel,
    sprints: pl.DataFrame,
    context: EventContext,
    sprint_date: date,
    rng: np.random.Generator,
    paths: int = race.PATHS,
    variant: backtest.Variant | None = None,
    grid: dict[str, int] | None = None,
) -> tuple[dict[str, race.Outcome], dict[str, float], int]:
    """Each weather scenario simulated over the sprint distance. The grid is the sprint
    classification's when the sprint has run, and the one passed in when it has not."""
    if grid is None:
        field = sprints.filter(
            (pl.col("season") == context.season) & (pl.col("round") == context.round)
        ).drop_nulls("driver_code")
        back = max(backtest.FIELD, field.height)
        grid = {
            str(row["driver_code"]): (int(row["grid"]) if int(row["grid"]) > 0 else back)
            for row in field.iter_rows(named=True)
        }
    morning = before_sprint(pane, context)
    laps = sprint_laps(sprints, pane, context)
    mix = backtest.scenario_weights(pane, context.circuit_id, sprint_date)
    keys = dict(
        zip(
            backtest.SCENARIOS,
            rng.integers(0, 2**63, size=len(backtest.SCENARIOS)),
            strict=True,
        )
    )
    outcomes: dict[str, race.Outcome] = {}
    for name in backtest.SCENARIOS:
        if mix.get(name, 0.0) <= 0.0:
            continue
        built = backtest.setup(morning, context, sprint_date, name, grid, variant)
        # one stint and no stop: the sprint has no mandatory change and nobody makes one
        built = built.model_copy(
            update={
                "laps": laps,
                "tyre": built.tyre.model_copy(
                    update={"stop_counts": [1.0, 0.0, 0.0, 0.0], "strategies": []}
                ),
            }
        )
        outcomes[name] = race.simulate(built, np.random.default_rng(int(keys[name])), paths, name)
    if not outcomes:
        raise backtest.NoForecastError("every scenario carries zero weight")
    return outcomes, mix, laps


def forecast(
    pane: backtest.Panel,
    sprints: pl.DataFrame,
    context: EventContext,
    sprint_date: date,
    rng: np.random.Generator,
    paths: int = race.PATHS,
    variant: backtest.Variant | None = None,
) -> race.Outcome:
    outcomes, mix, _ = scenarios(pane, sprints, context, sprint_date, rng, paths, variant)
    return race.blend(outcomes, mix, "blended")


def timing(
    races: pl.DataFrame | None, context: EventContext
) -> tuple[date | None, datetime | None]:
    if races is None or "sprint_date" not in races.columns:
        return None, None
    here = races.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).drop_nulls("sprint_date")
    if not here.height:
        return None, None
    day = here["sprint_date"][0]
    when = here["sprint_utc"][0] if "sprint_utc" in here.columns else None
    if isinstance(when, datetime) and when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return (day if isinstance(day, date) else date.fromisoformat(str(day))), when


def state(
    sprints: pl.DataFrame,
    pane: backtest.Panel,
    context: EventContext,
    races: pl.DataFrame | None,
    now: datetime,
) -> SprintState:
    day, when = timing(races, context)
    if day is None:
        return "no_sprint"
    held = sprints.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    ).height
    if held or (when is not None and when <= now):
        return "run"
    return "forecast" if live_grid(pane, context) else "awaiting_grid"


def live_grid(pane: backtest.Panel, context: EventContext) -> dict[str, int]:
    """Sprint qualifying read off the timing laps. Sprint grid drops are rare and not applied."""
    order = pane.sprint_quali.filter(
        (pl.col("season") == context.season) & (pl.col("round") == context.round)
    )
    return starting.knockout_grid(order) if order.height else {}


def live(
    pane: backtest.Panel,
    sprints: pl.DataFrame,
    context: EventContext,
    sprint_date: date,
    rng: np.random.Generator,
    pool: pooling.Pool | None,
    paths: int = race.PATHS,
) -> backtest.Forecast:
    """The sprint forecast for the morning of a sprint, pooled with the grid baseline the way
    the sprint backtest scored it."""
    grid = live_grid(pane, context)
    if not grid:
        raise backtest.NoForecastError(
            f"no sprint qualifying laps for {context.season}:{context.round}"
        )
    outcomes, mix, laps = scenarios(pane, sprints, context, sprint_date, rng, paths, grid=grid)
    blended = race.blend(outcomes, mix, "blended")
    codes = blended.driver_code
    width = len(blended.position[0])
    back = max(backtest.FIELD, len(codes))
    fitted = baselines.fit(
        pane.results.select(baselines.COLUMNS), sprint_date, known=pane.entries, width=width
    )
    prior = fitted.lookups["grid"].predict([grid.get(code, back) for code in codes], width)

    def apply(outcome: race.Outcome) -> race.Outcome:
        mixed = outcome.probabilities()
        if pool is not None:
            mixed = pooling.combine(mixed, prior, len(outcome.driver_code), pool)
        rank = np.arange(1, mixed.shape[1] + 1, dtype=np.float64)
        return outcome.model_copy(
            update={
                "position": [[float(value) for value in row] for row in mixed],
                "win": [float(row[0]) for row in mixed],
                "podium": [float(row[:3].sum()) for row in mixed],
                "points": [float(row[:SPRINT_POINTS].sum()) for row in mixed],
                "expected_position": [float(np.dot(row, rank)) for row in mixed],
            }
        )

    return backtest.Forecast(
        season=context.season,
        round=context.round,
        circuit_id=context.circuit_id,
        race_date=context.race_date,
        as_of=sprint_date,
        paths=paths,
        laps=laps,
        scenario_weights=mix,
        weights_are_forecast=False,
        outcome=apply(blended),
        scenarios={name: apply(outcome) for name, outcome in outcomes.items()},
        assumptions=[],
        pool=pool,
    )


def run(
    pane: backtest.Panel,
    sprints: pl.DataFrame,
    rng: np.random.Generator,
    run_id: str,
    paths: int = race.PATHS,
    seed: int = 0,
    warmup: int = WARMUP,
    until: date | None = None,
    variant: backtest.Variant | None = None,
) -> backtest.Report:
    events = pane.events.select("season", "round", "circuit_id", "race_name", "start_utc")
    calendar = (
        sprints.select("season", "round", "race_date")
        .unique()
        .join(events, on=["season", "round"], how="inner")
        .sort("race_date")
    )
    if until is not None:
        calendar = calendar.filter(pl.col("race_date") < until)
    if not calendar.height:
        raise backtest.NoForecastError("no sprints to score")
    race_history = pane.results.select(baselines.COLUMNS)
    names = (backtest.MODEL, backtest.RAW, *baselines.FEATURES)
    grids: dict[str, list[np.ndarray]] = {name: [] for name in names}
    truth: list[np.ndarray] = []
    labels: list[np.ndarray] = []
    per_race: list[backtest.RaceScore] = []
    history: list[pooling.Scored] = []
    width = backtest.FIELD
    for index, row in enumerate(calendar.iter_rows(named=True)):
        when: date = row["race_date"]
        context = EventContext(
            season=int(row["season"]),
            round=int(row["round"]),
            circuit_id=str(row["circuit_id"]),
            race_name=str(row["race_name"]),
            race_date=when,
            start_utc=row["start_utc"],
        )
        here = sprints.filter(
            (pl.col("season") == context.season) & (pl.col("round") == context.round)
        ).drop_nulls(["driver_code", "position"])
        finished = {str(r["driver_code"]): int(r["position"]) for r in here.iter_rows(named=True)}
        if len(finished) < 2:
            continue
        try:
            outcome = forecast(
                pane,
                sprints,
                context,
                when,
                backtest.race_rng(seed, context.season, context.round),
                paths,
                variant,
            )
        except (backtest.NoForecastError, track_fit.UnknownCircuitError, form.NoPairingsError):
            continue
        codes = [code for code in outcome.driver_code if code in finished]
        combined = pl.concat(
            [
                race_history.filter(pl.col("race_date") < when),
                here.select(baselines.COLUMNS),
            ]
        )
        try:
            field = baselines.entries(combined, context.season, context.round)
        except baselines.NoHistoryError:
            continue
        order = {code: slot for slot, code in enumerate(field.driver_code)}
        if any(code not in order for code in codes) or len(codes) < 2:
            continue
        width = max(width, field.width)
        starters = len(outcome.driver_code)
        seen = {code: slot for slot, code in enumerate(outcome.driver_code)}
        sim = backtest.renormalised(
            outcome.probabilities()[[seen[c] for c in codes]], starters, width
        )
        fitted = baselines.fit(race_history, when, known=pane.entries, width=field.width)
        guessed = {
            name: backtest.renormalised(values[[order[c] for c in codes]], starters, width)
            for name, values in fitted.predict(field).items()
        }
        actual = np.array([finished[code] for code in codes]) - 1
        scored = pooling.Scored(sim=sim, grid=guessed["grid"], actual=actual, starters=starters)
        if index < warmup:
            history.append(scored)
            continue
        chosen = pooling.fit(history)
        grids[backtest.MODEL].append(pooling.combine(sim, guessed["grid"], starters, chosen))
        grids[backtest.RAW].append(sim)
        for name in baselines.FEATURES:
            grids[name].append(guessed[name])
        history.append(scored)
        truth.append(actual)
        labels.append(np.full(len(codes), index))
        per_race.append(
            backtest.RaceScore(
                season=context.season,
                round=context.round,
                circuit_id=context.circuit_id,
                race_date=when,
                starters=starters,
                log_loss={name: log_loss(grids[name][-1], actual) for name in names},
            )
        )
    if not truth:
        raise backtest.NoForecastError("no sprint produced both a forecast and a result")
    stacked = {name: np.vstack([_wide(g, width) for g in grids[name]]) for name in names}
    outcome_all = np.concatenate(truth)
    race_of = np.concatenate(labels)
    scored_models = [
        backtest.score_model(name, stacked[name], outcome_all, race_of, rng) for name in names
    ]
    ours = next(item for item in scored_models if item.name == backtest.MODEL)
    paired = [
        backtest.paired_gain(
            name, stacked[backtest.MODEL], stacked[name], outcome_all, race_of, rng
        )
        for name in baselines.FEATURES
    ]
    return backtest.Report(
        generated_at=datetime.now(UTC),
        run_id=run_id,
        from_season=int(str(calendar["season"].min())),
        holdout=len(per_race),
        paths=paths,
        field=width,
        seed=seed,
        scored=scored_models,
        paired=paired,
        per_race=per_race,
        beats_baselines=all(
            ours.log_loss.value < item.log_loss.value
            for item in scored_models
            if item.name in baselines.FEATURES
        ),
        separated_from=[
            item.baseline
            for item in paired
            if np.isfinite(item.log_loss_gain.low) and item.log_loss_gain.low > 0.0
        ],
        assumptions=[],
        warmup=warmup,
        until=until,
        stage="sprint",
        pool=pooling.fit(history),
    )


def _wide(grid: np.ndarray, width: int) -> np.ndarray:
    if grid.shape[1] >= width:
        return grid
    out = np.zeros((grid.shape[0], width))
    out[:, : grid.shape[1]] = grid
    return out
