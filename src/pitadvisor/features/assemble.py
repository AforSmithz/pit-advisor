# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import polars as pl
from pydantic import BaseModel

from pitadvisor.features import form, quali_race, reliability, track_fit
from pitadvisor.features import weather as wet_weather
from pitadvisor.features.clean_pace import (
    Regime,
    SessionPace,
    UnidentifiableFitError,
    fit_session,
)
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.quality.checks import read_table
from pitadvisor.types import Layer, SessionKind

PACE_COLUMNS = (
    "season",
    "round",
    "race_date",
    "circuit_id",
    "driver_code",
    "constructor_id",
    "regime",
    "is_wet",
    "value",
)


class NoEventError(RuntimeError):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class EventContext(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    race_name: str
    race_date: date
    start_utc: datetime | None = None


class Coverage(BaseModel, frozen=True):
    """What the metrics are standing on. §6.2 wants staleness on the page, not in a log."""

    races_in_lake: int
    sessions_fitted: int
    sessions_skipped: int
    dry_sessions: int
    wet_sessions: int
    clean_laps: int
    total_laps: int
    exclusions: dict[str, int]
    exclusion_rate: float
    drivers_rated: int
    quali_events: int


class EventMetrics(BaseModel, frozen=True):
    context: EventContext
    as_of: date
    generated_at: datetime
    run_id: str
    coverage: Coverage
    form: form.FormFit
    quali: quali_race.Trend
    track: track_fit.TrackFit
    weather: wet_weather.ScenarioWeights | None
    wet: wet_weather.WetForm
    reliability: reliability.ReliabilityFit


@dataclass(frozen=True)
class Assembled:
    metrics: EventMetrics
    pace: pl.DataFrame
    quali: list[tuple[date, quali_race.EventQualiRace]]


def races(store: ObjectStore, layer: Layer = Layer.BRONZE) -> pl.DataFrame:
    frame = read_table(store, layer, "races")
    if frame is None:
        raise NoEventError("no races in bronze, ingest jolpica first")
    return frame.select(
        "season", "round", "circuit_id", "race_name", "race_date", "start_utc"
    ).unique(subset=["season", "round"], keep="last")


def _context(row: dict[str, object]) -> EventContext:
    return EventContext(
        season=int(str(row["season"])),
        round=int(str(row["round"])),
        circuit_id=str(row["circuit_id"]),
        race_name=str(row["race_name"]),
        race_date=row["race_date"],  # pyright: ignore[reportArgumentType]
        start_utc=row["start_utc"],  # pyright: ignore[reportArgumentType]
    )


def next_event(store: ObjectStore, today: date | None = None) -> EventContext:
    cutoff = today or datetime.now(UTC).date()
    ahead = races(store).filter(pl.col("race_date") >= cutoff).sort("race_date")
    if not ahead.height:
        # out of season, or the calendar has not landed yet: the last race is the one that
        # every metric is still anchored on, so predicting it is the honest fallback
        behind = races(store).sort("race_date")
        if not behind.height:
            raise NoEventError("no races in bronze")
        return _context(behind.row(-1, named=True))
    return _context(ahead.row(0, named=True))


def event_at(store: ObjectStore, season: int, round_: int) -> EventContext:
    found = races(store).filter((pl.col("season") == season) & (pl.col("round") == round_))
    if not found.height:
        raise NoEventError(f"{season} round {round_} is not in bronze")
    return _context(found.row(0, named=True))


def session_paces(
    store: ObjectStore,
    session: SessionKind = SessionKind.RACE,
    layer: Layer = Layer.BRONZE,
) -> tuple[list[SessionPace], int]:
    """Both regimes on every race: a mixed race yields a dry fit and a wet one, and either
    can come back empty without that being a failure."""
    laps = read_table(store, layer, "session_laps")
    if laps is None:
        return [], 0
    wanted = laps.filter(pl.col("session") == str(session))
    found: list[SessionPace] = []
    skipped = 0
    for _, rows in wanted.group_by("season", "round"):
        for regime in (Regime.DRY, Regime.WET):
            try:
                fitted = fit_session(rows, regime=regime)
            except UnidentifiableFitError:
                skipped += 1
                continue
            if fitted is None:
                skipped += 1
                continue
            found.append(fitted)
    return sorted(found, key=lambda pace: (pace.season, pace.round, pace.regime)), skipped


def pace_frame(
    paces: list[SessionPace], results: pl.DataFrame, events: pl.DataFrame
) -> pl.DataFrame:
    rows = [
        {
            "season": pace.season,
            "round": pace.round,
            "driver_code": driver.driver_code,
            "regime": str(pace.regime),
            "is_wet": pace.regime is Regime.WET,
            "value": driver.percent_off_benchmark,
        }
        for pace in paces
        for driver in pace.drivers
    ]
    if not rows:
        return pl.DataFrame(schema={name: pl.String for name in PACE_COLUMNS})
    seats = results.select("season", "round", "driver_code", "constructor_id").drop_nulls()
    return (
        pl.DataFrame(rows)
        .join(seats, on=["season", "round", "driver_code"], how="inner")
        .join(events.select("season", "round", "race_date", "circuit_id"), on=["season", "round"])
        .select(PACE_COLUMNS)
    )


def quali_frame(qualifying: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    return (
        qualifying.drop_nulls("driver_code")
        .join(events.select("season", "round", "race_date"), on=["season", "round"])
        .select(quali_race.COLUMNS)
    )


def quali_events(
    qualifying: pl.DataFrame, paces: list[SessionPace]
) -> list[tuple[date, quali_race.EventQualiRace]]:
    dry = {(pace.season, pace.round): pace for pace in paces if pace.regime is Regime.DRY}
    found: list[tuple[date, quali_race.EventQualiRace]] = []
    for key, rows in qualifying.group_by("season", "round"):
        season, round_ = int(str(key[0])), int(str(key[1]))
        try:
            fitted = quali_race.fit_event(rows, dry.get((season, round_)))
        except quali_race.NoQualifyingLapError:
            continue
        found.append((rows["race_date"][0], fitted))
    return sorted(found, key=lambda item: item[0])


def team_frame(pace: pl.DataFrame) -> pl.DataFrame:
    return (
        pace.filter(~pl.col("is_wet"))
        .group_by("season", "round", "race_date", "circuit_id", "constructor_id")
        .agg(pl.col("value").mean())
        .select(track_fit.COLUMNS)
    )


def reliability_frame(results: pl.DataFrame, events: pl.DataFrame) -> pl.DataFrame:
    return results.join(
        events.select("season", "round", "race_date"), on=["season", "round"]
    ).select(reliability.COLUMNS)


def _weather(
    store: ObjectStore, context: EventContext, layer: Layer
) -> wet_weather.ScenarioWeights | None:
    if context.start_utc is None:
        return None
    frame = read_table(store, layer, "weather")
    if frame is None:
        return None
    covering = frame.filter(pl.col("circuit_id") == context.circuit_id)
    if not covering.height:
        return None
    try:
        return wet_weather.scenario(covering, context.start_utc)
    except wet_weather.NoForecastError:
        return None


def assemble(
    store: ObjectStore,
    context: EventContext,
    run_id: str,
    layer: Layer = Layer.BRONZE,
    as_of: date | None = None,
    generated_at: datetime | None = None,
) -> Assembled:
    cutoff = as_of or context.race_date
    events = races(store, layer)
    results = read_table(store, layer, "results")
    qualifying = read_table(store, layer, "qualifying")
    if results is None or qualifying is None:
        raise NoEventError("results and qualifying must both be in bronze")

    paces, skipped = session_paces(store, layer=layer)
    pace = pace_frame(paces, results, events)
    quali = quali_frame(qualifying, events)
    stacked = quali_events(quali.filter(pl.col("race_date") < cutoff), paces)

    dry = pace.filter(~pl.col("is_wet"))
    metrics = EventMetrics(
        context=context,
        as_of=cutoff,
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        coverage=Coverage(
            races_in_lake=events.height,
            sessions_fitted=len(paces),
            sessions_skipped=skipped,
            dry_sessions=sum(1 for item in paces if item.regime is Regime.DRY),
            wet_sessions=sum(1 for item in paces if item.regime is Regime.WET),
            clean_laps=sum(item.clean_laps for item in paces),
            total_laps=sum(item.total_laps for item in paces),
            exclusions=_pooled(paces),
            exclusion_rate=_pooled_rate(paces),
            drivers_rated=dry.select("driver_code").n_unique(),
            quali_events=len(stacked),
        ),
        form=form.fit(dry.select(form.COLUMNS), cutoff),
        quali=quali_race.trend(stacked, cutoff),
        track=track_fit.fit(team_frame(pace), context.circuit_id, cutoff),
        weather=_weather(store, context, layer),
        wet=wet_weather.wet_form(pace.select(wet_weather.COLUMNS), cutoff),
        reliability=reliability.fit(reliability_frame(results, events), cutoff),
    )
    return Assembled(metrics=metrics, pace=pace, quali=stacked)


def _pooled(paces: list[SessionPace]) -> dict[str, int]:
    counted: dict[str, int] = {}
    for pace in paces:
        for reason, count in pace.exclusions.items():
            counted[reason] = counted.get(reason, 0) + count
    return dict(sorted(counted.items()))


def _pooled_rate(paces: list[SessionPace]) -> float:
    total = sum(pace.total_laps for pace in paces)
    if not total:
        return 0.0
    return sum(pace.total_laps - pace.clean_laps for pace in paces) / total


def stale_by(metrics: EventMetrics, today: date | None = None) -> timedelta:
    return (today or datetime.now(UTC).date()) - metrics.as_of
