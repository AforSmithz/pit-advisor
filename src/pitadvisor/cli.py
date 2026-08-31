import json
from collections.abc import Callable
from datetime import UTC, date, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import numpy as np
import typer
from boto3.session import Session
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from pitadvisor.agent import evals as agent_evals
from pitadvisor.agent import kb as agent_kb
from pitadvisor.agent import tools as agent_tools
from pitadvisor.agent.runtime import agent_for
from pitadvisor.config import Settings, boto_session, get_settings
from pitadvisor.features import assemble as feature_assemble
from pitadvisor.ingest import docs as doc_corpus
from pitadvisor.ingest import jolpica
from pitadvisor.ingest.fastf1_session import backfill as session_backfill
from pitadvisor.ingest.fastf1_session import ingest_session
from pitadvisor.ingest.ratelimit import (
    HOURLY_CAPS,
    JOLPICA_HOURLY_CAP,
    Bucket,
    DynamoBucket,
    DynamoLedger,
    Ledger,
    LocalBucket,
    LocalLedger,
    QuotaExhaustedError,
    RateLimiter,
)
from pitadvisor.ingest.raw_store import LocalObjectStore, ObjectStore, RawStore, object_store
from pitadvisor.ingest.rebuild import rebuild_bronze
from pitadvisor.ingest.weather import Circuit, WeatherClient, event_circuits
from pitadvisor.ingest.weather import ingest_event as weather_ingest_event
from pitadvisor.model import backtest as forecast_model
from pitadvisor.model import calibrate
from pitadvisor.outputs.view_contracts import (
    Evidence,
    ForecastView,
    calibration_view,
    driver_view,
    emit,
    evidence_from,
    forecast_view,
    pipeline_view,
    track_view,
    weekend_view,
)
from pitadvisor.quality import catalog, checks, lineage
from pitadvisor.types import EventKey, IngestOutcome, Layer, SessionKey, SessionKind, Source

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="F1 race-weekend advisor.",
)


class CheckResult(BaseModel, frozen=True):
    name: str
    ok: bool
    detail: str


# boto3 ships no type information, so every client is Any and the Any stops here
def _client(session: Session, service: str, region: str | None = None) -> Any:
    return cast(Any, session).client(service, region_name=region)


def _detail(exc: BaseException) -> str:
    if isinstance(exc, ClientError):
        error: Any = cast(Any, exc).response.get("Error", {})
        code: Any = error.get("Code")
        return str(code) if code else "ClientError"
    if isinstance(exc, BotoCoreError):
        return type(exc).__name__
    return f"{type(exc).__name__}: {exc}"


def check_credentials(session: Session, settings: Settings) -> CheckResult:
    account = _client(session, "sts").get_caller_identity()["Account"]
    if account != settings.account_id:
        return CheckResult(
            name="credentials",
            ok=False,
            detail=f"signed in to {account}, expected {settings.account_id}",
        )
    return CheckResult(name="credentials", ok=True, detail=f"account {account}")


def check_region(session: Session, settings: Settings) -> CheckResult:
    region: Any = cast(Any, session).region_name
    if region != settings.aws_region:
        return CheckResult(
            name="region",
            ok=False,
            detail=f"{region or 'unset'}, expected {settings.aws_region}",
        )
    return CheckResult(name="region", ok=True, detail=settings.aws_region)


def check_data_bucket(session: Session, settings: Settings) -> CheckResult:
    _client(session, "s3").head_bucket(Bucket=settings.data_bucket)
    return CheckResult(name="data bucket", ok=True, detail=settings.data_bucket)


def check_glue_database(session: Session, settings: Settings) -> CheckResult:
    _client(session, "glue").get_database(Name=settings.glue_database)
    return CheckResult(name="glue database", ok=True, detail=settings.glue_database)


def check_athena_workgroup(session: Session, settings: Settings) -> CheckResult:
    group: Any = _client(session, "athena").get_work_group(WorkGroup=settings.athena_workgroup)[
        "WorkGroup"
    ]
    cap: Any = group.get("Configuration", {}).get("BytesScannedCutoffPerQuery")
    if not cap:
        return CheckResult(
            name="athena workgroup",
            ok=False,
            detail=f"{settings.athena_workgroup} has no per-query scan cap",
        )
    if int(cap) > settings.max_scanned_bytes:
        return CheckResult(
            name="athena workgroup",
            ok=False,
            detail=f"cap {cap} is above the configured {settings.max_scanned_bytes} bytes",
        )
    return CheckResult(
        name="athena workgroup",
        ok=True,
        detail=f"{settings.athena_workgroup} capped at {cap} bytes",
    )


def check_budget(session: Session, settings: Settings) -> CheckResult:
    # budgets is a global api and only answers in us-east-1
    budgets = _client(session, "budgets", region="us-east-1")
    budget: Any = budgets.describe_budget(
        AccountId=settings.account_id,
        BudgetName=settings.budget_name,
    )["Budget"]
    limit: Any = budget.get("BudgetLimit", {})
    amount = f"{limit.get('Amount', '?')} {limit.get('Unit', '')}".strip()
    notifications: Any = budgets.describe_notifications_for_budget(
        AccountId=settings.account_id,
        BudgetName=settings.budget_name,
    ).get("Notifications", [])
    if not notifications:
        return CheckResult(
            name="budget",
            ok=False,
            detail=f"{settings.budget_name} at {amount} but nobody is subscribed to it",
        )
    return CheckResult(name="budget", ok=True, detail=f"{settings.budget_name} at {amount}")


CHECKS: tuple[tuple[str, Callable[[Session, Settings], CheckResult]], ...] = (
    ("credentials", check_credentials),
    ("region", check_region),
    ("data bucket", check_data_bucket),
    ("glue database", check_glue_database),
    ("athena workgroup", check_athena_workgroup),
    ("budget", check_budget),
)


def run_checks(settings: Settings) -> list[CheckResult]:
    try:
        session = boto_session(settings)
    except BotoCoreError as exc:
        return [CheckResult(name="credentials", ok=False, detail=_detail(exc))]
    results: list[CheckResult] = []
    for name, check in CHECKS:
        try:
            results.append(check(session, settings))
        except Exception as exc:
            results.append(CheckResult(name=name, ok=False, detail=_detail(exc)))
    return results


def render(results: list[CheckResult]) -> None:
    width = max(len(result.name) for result in results)
    for result in results:
        marker = "ok  " if result.ok else "FAIL"
        typer.echo(f"{marker}  {result.name.ljust(width)}  {result.detail}")


@app.command(help="Print the installed package version.")
def version() -> None:
    typer.echo(package_version("pitadvisor"))


@app.command(help="Check that this machine can reach the project's AWS account.")
def doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Emit results as JSON.")] = False,
) -> None:
    results = run_checks(get_settings())
    if as_json:
        typer.echo(json.dumps([result.model_dump() for result in results], indent=2))
    else:
        render(results)
    if not all(result.ok for result in results):
        raise typer.Exit(1)


LOCAL_ROOT = Path("data/local")


def _runtime(
    local: bool, settings: Settings
) -> tuple[ObjectStore, Ledger, Callable[[str], Bucket]]:
    """Store, ledger, and a bucket per upstream: the two sources have separate budgets."""
    if local:
        LOCAL_ROOT.mkdir(parents=True, exist_ok=True)

        def local_bucket(name: str) -> Bucket:
            return LocalBucket(
                LOCAL_ROOT / "quota.json",
                name=name,
                capacity=HOURLY_CAPS[name],
                refill_per_second=HOURLY_CAPS[name] / 3600,
            )

        return LocalObjectStore(LOCAL_ROOT), LocalLedger(LOCAL_ROOT / "ledger.json"), local_bucket

    dynamo = _client(boto_session(settings), "dynamodb", settings.aws_region)

    def dynamo_bucket(name: str) -> Bucket:
        return DynamoBucket(
            settings.ledger_table,
            dynamo,
            name=name,
            capacity=HOURLY_CAPS[name],
            refill_per_second=HOURLY_CAPS[name] / 3600,
        )

    return object_store(settings), DynamoLedger(settings.ledger_table, dynamo), dynamo_bucket


def _run_id() -> str:
    return f"{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}-{uuid4().hex[:6]}"


def _render_outcomes(outcomes: list[IngestOutcome]) -> None:
    for outcome in outcomes:
        if outcome.skipped:
            typer.echo(f"skip  {outcome.table:<13} r{outcome.round:02d}  {outcome.skipped}")
            continue
        cached = " (304)" if outcome.not_modified else ""
        typer.echo(
            f"ok    {outcome.table:<13} r{outcome.round:02d}  {outcome.rows} rows, "
            f"{outcome.quarantined} quarantined, {outcome.requests} requests{cached}"
        )


@app.command(help="Pull one source into raw and bronze.")
def ingest(
    source: Annotated[Source, typer.Option(help="Which upstream to pull.")] = Source.JOLPICA,
    season: Annotated[int, typer.Option(help="Championship year.")] = 2024,
    round_: Annotated[int | None, typer.Option("--round", help="One event only.")] = None,
    session: Annotated[SessionKind | None, typer.Option(help="Session, fastf1 only.")] = None,
    with_laps: Annotated[
        bool, typer.Option("--with-laps", help="Include per-lap timings, jolpica only.")
    ] = False,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Print the plan, fetch nothing.")
    ] = False,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
) -> None:
    settings = get_settings()
    run_id = _run_id()
    if dry_run:
        rounds = (
            [round_]
            if round_
            else [circuit.round for circuit in _known_rounds(local, settings, season)]
        )
        urls = jolpica.plan(season, rounds or [])
        for url in urls:
            typer.echo(f"GET {url}")
        if not rounds:
            typer.echo(
                "rounds unknown until the schedule lands, only the season request is planned"
            )
        typer.echo(f"{len(urls)} requests against a {JOLPICA_HOURLY_CAP}/hour cap")
        return
    store, ledger, bucket_for = _runtime(local, settings)
    if source is Source.JOLPICA:
        limiter = RateLimiter(bucket_for("jolpica"))
        client = jolpica.JolpicaClient(RawStore(store), ledger, limiter, run_id)
        resources = _resources(with_laps)
        if round_ is None:
            outcomes = jolpica.backfill(client, store, season, resources)
        else:
            outcomes = jolpica.ingest_event(
                client, store, EventKey(season=season, round=round_), resources
            )
    elif source is Source.OPEN_METEO:
        limiter = RateLimiter(bucket_for("open_meteo"))
        outcomes = _ingest_weather(store, ledger, limiter, run_id, season, round_)
    elif source is Source.FASTF1:
        if round_ is None or session is None:
            raise typer.BadParameter("fastf1 needs both --round and --session")
        key = SessionKey(season=season, round=round_, session=session)
        outcomes = [ingest_session(store, key, settings.fastf1_cache, run_id, sync_cache=not local)]
    else:
        raise typer.BadParameter(f"{source} has no ingest path yet")
    _render_outcomes(outcomes)


def _resources(with_laps: bool) -> tuple[str, ...]:
    """Laps are four times the requests of everything else, so they are opt in."""
    if with_laps:
        return jolpica.RESOURCES
    return tuple(name for name in jolpica.RESOURCES if name != "laps")


def _known_rounds(local: bool, settings: Settings, season: int) -> list[Circuit]:
    try:
        store, _, _ = _runtime(local, settings)
        return event_circuits(store, season)
    except Exception:
        return []


def _ingest_weather(
    store: ObjectStore,
    ledger: Ledger,
    limiter: RateLimiter,
    run_id: str,
    season: int,
    round_: int | None,
) -> list[IngestOutcome]:
    circuits = [c for c in event_circuits(store, season) if round_ is None or c.round == round_]
    if not circuits:
        raise typer.BadParameter("no circuits in bronze yet, ingest jolpica races first")
    client = WeatherClient(RawStore(store), ledger, limiter, run_id)
    return [
        weather_ingest_event(
            client,
            store,
            EventKey(season=circuit.season, round=circuit.round),
            circuit.circuit_id,
            circuit.latitude,
            circuit.longitude,
            circuit.race_date,
        )
        for circuit in circuits
    ]


@app.command(help="Walk a range of seasons into raw and bronze, resuming where it stopped.")
def backfill(
    from_: Annotated[int, typer.Option("--from", help="First season.")],
    to: Annotated[int, typer.Option("--to", help="Last season, inclusive.")],
    source: Annotated[Source, typer.Option(help="Which upstream to walk.")] = Source.JOLPICA,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    with_laps: Annotated[
        bool, typer.Option("--with-laps", help="Include per-lap timings, jolpica only.")
    ] = False,
    sessions: Annotated[
        list[SessionKind] | None,
        typer.Option("--session", help="Session to pull, repeatable, fastf1 only."),
    ] = None,
) -> None:
    settings = get_settings()
    if source is Source.FASTF1:
        _backfill_sessions(from_, to, local, settings, sessions)
        return
    if source is not Source.JOLPICA:
        raise typer.BadParameter(f"{source} has no backfill path yet")
    store, ledger, bucket_for = _runtime(local, settings)
    limiter = RateLimiter(bucket_for("jolpica"))
    client = jolpica.JolpicaClient(RawStore(store), ledger, limiter, _run_id())
    resources = _resources(with_laps)
    done: list[IngestOutcome] = []
    for season in range(from_, to + 1):
        try:
            done.extend(jolpica.backfill(client, store, season, resources))
        except QuotaExhaustedError as exc:
            _render_outcomes(done)
            typer.echo(f"stopped in {season}: {exc}. rerun to resume, raw and bronze are kept")
            raise typer.Exit(0) from exc
    _render_outcomes(done)


def _backfill_sessions(
    from_: int, to: int, local: bool, settings: Settings, sessions: list[SessionKind] | None
) -> None:
    store, _, _ = _runtime(local, settings)
    kinds = tuple(sessions) if sessions else (SessionKind.RACE,)
    run_id = _run_id()
    for season in range(from_, to + 1):
        typer.echo(f"season {season}")
        # a cold fastf1 cache makes this hours long, so each season prints as it lands
        _render_outcomes(
            session_backfill(
                store, season, settings.fastf1_cache, run_id, kinds, sync_cache=not local
            )
        )


@app.command(help="Replay raw into bronze with no network at all.")
def rebuild(
    layer: Annotated[Layer, typer.Option(help="Which layer to rebuild.")] = Layer.BRONZE,
    season: Annotated[int | None, typer.Option(help="One season only.")] = None,
    source: Annotated[Source | None, typer.Option(help="One upstream only.")] = None,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
) -> None:
    if layer is not Layer.BRONZE:
        raise typer.BadParameter(f"only bronze replays from raw, not {layer}")
    settings = get_settings()
    store = LocalObjectStore(LOCAL_ROOT) if local else object_store(settings)
    outcomes = rebuild_bronze(store, _run_id(), season, source)
    if not outcomes:
        typer.echo("nothing in raw matched")
        raise typer.Exit(0)
    _render_outcomes(outcomes)
    typer.echo(f"{sum(o.rows for o in outcomes)} rows from {len(outcomes)} partitions, 0 requests")


@app.command(name="quality-report", help="Contract, freshness and referential checks over a layer.")
def quality_report(
    layer: Annotated[Layer, typer.Option(help="Which layer to check.")] = Layer.BRONZE,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the report as JSON.")] = False,
) -> None:
    store, _, _ = _runtime(local, get_settings())
    result = checks.report(store, layer)
    if as_json:
        typer.echo(result.model_dump_json(indent=2))
    else:
        for outcome in result.outcomes:
            marker = {"ok": "ok  ", "warn": "warn", "fail": "FAIL"}[outcome.status]
            typer.echo(f"{marker}  {outcome.table:<13} {outcome.check:<13} {outcome.detail}")
        for item in result.quarantine:
            tag = "known" if item.explained else "UNEXPLAINED"
            typer.echo(f"quar  {item.table:<13} {item.reason:<20} {item.rows} rows ({tag})")
        for item in result.diagnostics:
            typer.echo(f"diag  {item.table:<13} {item.name:<13} {item.detail}")
    if not result.ok:
        raise typer.Exit(1)


EVENT_VIEWS = {"weekend": weekend_view, "driver": driver_view, "track": track_view}
SIM_VIEWS = ("forecast", "calibration")
RESULTS = Path("results/backtest")


def _assembled(store: ObjectStore, event: str) -> feature_assemble.Assembled:
    try:
        return feature_assemble.assemble(store, _event(store, event), _run_id())
    except feature_assemble.NoEventError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def _event(store: ObjectStore, event: str) -> feature_assemble.EventContext:
    if event == "next":
        return feature_assemble.next_event(store)
    try:
        season, round_ = (int(part) for part in event.split(":", 1))
    except ValueError as exc:
        raise typer.BadParameter("--event takes 'next' or 'season:round'") from exc
    return feature_assemble.event_at(store, season, round_)


@app.command(name="emit-views", help="Write the versioned view artifacts the dashboard reads.")
def emit_views(
    views: Annotated[str, typer.Option(help="Comma separated view names.")] = "pipeline",
    event: Annotated[str, typer.Option(help="'next' or 'season:round'.")] = "next",
    paths: Annotated[int, typer.Option(help="Simulated races behind the forecast view.")] = 4000,
    seed: Annotated[int, typer.Option(help="Seed, so the view reproduces.")] = 11,
    results: Annotated[Path, typer.Option(help="Where backtest.json lives.")] = RESULTS,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
) -> None:
    settings = get_settings()
    store, _, bucket_for = _runtime(local, settings)
    wanted = [name.strip() for name in views.split(",") if name.strip()]
    unknown = set(wanted) - {"pipeline"} - set(EVENT_VIEWS) - set(SIM_VIEWS)
    if unknown:
        raise typer.BadParameter(f"no emitter yet for {', '.join(sorted(unknown))}")
    if "pipeline" in wanted:
        report = checks.report(store, Layer.BRONZE)
        quota = [bucket_for(name).state() for name in HOURLY_CAPS]
        typer.echo(emit(store, pipeline_view(report, _run_id(), quota)))
    needed = [name for name in wanted if name in EVENT_VIEWS]
    if needed:
        # every event view comes off one assembly, which is the expensive part
        assembled = _assembled(store, event)
        for name in needed:
            typer.echo(emit(store, EVENT_VIEWS[name](assembled)))
    if "calibration" in wanted:
        typer.echo(emit(store, calibration_view(_report(results))))
    if "forecast" in wanted:
        typer.echo(emit(store, _forecast(store, event, paths, seed, results)))


def _report(results: Path) -> forecast_model.Report:
    source = results / calibrate.REPORT
    if not source.exists():
        typer.echo(f"{source} is not there, run 'pitadv backtest' first", err=True)
        raise typer.Exit(1)
    return forecast_model.Report.model_validate_json(source.read_text())


def _evidence(results: Path) -> Evidence | None:
    source = results / calibrate.REPORT
    if not source.exists():
        return None
    return evidence_from(forecast_model.Report.model_validate_json(source.read_text()))


def _forecast(store: ObjectStore, event: str, paths: int, seed: int, results: Path) -> ForecastView:
    pane = _panel(store)
    context = _event(store, event)
    # the archived weather of a race that has already run is what happened, not a forecast.
    # taking it would tell the simulation whether it rained, so only a real forecast is used
    observed = feature_assemble.weather_for(store, context)
    ahead = observed is not None and observed.is_forecast
    mix = (
        {"dry": observed.dry, "mixed": observed.mixed, "wet": observed.wet}
        if ahead and observed is not None
        else None
    )
    try:
        predicted = forecast_model.forecast(
            pane,
            context,
            context.race_date,
            np.random.default_rng(seed),
            paths=paths,
            weights=mix,
            weights_are_forecast=ahead,
        )
    except forecast_model.NoForecastError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    seats, _ = forecast_model.seats_for(pane, context, context.race_date)
    return forecast_view(
        predicted,
        context,
        _run_id(),
        seats,
        forecast_model.grid_for(pane, context, predicted.outcome.driver_code),
        _evidence(results),
    )


@app.command(help="Fit the feature stack for one event and print what it stands on.")
def metrics(
    event: Annotated[str, typer.Option(help="'next' or 'season:round'.")] = "next",
    explain: Annotated[
        bool, typer.Option("--explain", help="Print the exclusion reason counts.")
    ] = False,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    as_json: Annotated[bool, typer.Option("--json", help="Emit the metrics as JSON.")] = False,
) -> None:
    store, _, _ = _runtime(local, get_settings())
    assembled = _assembled(store, event)
    if as_json:
        typer.echo(assembled.metrics.model_dump_json(indent=2))
        return
    _render_metrics(assembled.metrics, explain)


def _render_metrics(metrics: feature_assemble.EventMetrics, explain: bool) -> None:
    context, coverage = metrics.context, metrics.coverage
    typer.echo(f"{context.season} r{context.round:02d}  {context.race_name}  {context.circuit_id}")
    typer.echo(f"as of {metrics.as_of}, run {metrics.run_id}")
    typer.echo(
        f"pace       {coverage.sessions_fitted} fits "
        f"({coverage.dry_sessions} dry, {coverage.wet_sessions} wet), "
        f"{coverage.sessions_skipped} skipped, "
        f"{coverage.clean_laps} of {coverage.total_laps} fit-laps clean"
    )
    for reason, count in coverage.skips.items():
        typer.echo(f"           {count:>4} skipped, {reason}")
    typer.echo(
        f"form       {len(metrics.form.drivers)} drivers over {metrics.form.events_used} events, "
        f"{metrics.form.components} components, {metrics.form.flagged_pairs} pairs flagged"
    )
    typer.echo(
        f"quali      {len(metrics.quali.drivers)} drivers over {metrics.quali.events_used} events"
    )
    typer.echo(
        f"track      {len(metrics.track.regression)} teams, "
        f"{len(metrics.track.disagreements)} disagreements, "
        f"nearest {', '.join(item.circuit_id for item in metrics.track.neighbours[1:4])}"
    )
    if metrics.weather:
        weights = metrics.weather
        typer.echo(
            f"weather    dry {weights.dry:.2f} mixed {weights.mixed:.2f} wet {weights.wet:.2f}, "
            f"snapshot {weights.snapshot_at:%Y-%m-%d %H:%M}"
        )
    else:
        typer.echo("weather    no forecast covers the session window")
    typer.echo(
        f"wet pace   {len(metrics.wet.drivers)} drivers rated over "
        f"{metrics.wet.wet_sessions} wet sessions"
    )
    typer.echo(
        f"dnf        {len(metrics.reliability.teams)} team hazards, "
        f"cause coverage {metrics.reliability.cause_coverage:.0%}"
    )
    if not explain:
        return
    typer.echo(f"\nexclusions ({coverage.exclusion_rate:.0%} of fit-laps)")
    for reason, count in coverage.exclusions.items():
        share = count / coverage.total_laps if coverage.total_laps else 0.0
        typer.echo(f"  {reason:<18} {count:>7}  {share:>6.1%}")


def _panel(store: ObjectStore) -> forecast_model.Panel:
    try:
        return forecast_model.panel(store)
    except feature_assemble.NoEventError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


@app.command(help="Walk the calendar forward, score the simulation against every baseline.")
def backtest(
    from_season: Annotated[
        int, typer.Option("--from", help="Earliest season the holdout may reach back to.")
    ] = 2021,
    holdout: Annotated[int, typer.Option(help="How many of the most recent races to score.")] = 60,
    paths: Annotated[int, typer.Option(help="Simulated races per event.")] = 4000,
    seed: Annotated[int, typer.Option(help="Seed, so the report reproduces.")] = 11,
    output: Annotated[Path, typer.Option(help="Where the artifacts land.")] = RESULTS,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
) -> None:
    store, _, _ = _runtime(local, get_settings())
    pane = _panel(store)
    try:
        report = forecast_model.run(
            pane,
            from_season,
            holdout,
            np.random.default_rng(seed),
            _run_id(),
            paths=paths,
            seed=seed,
        )
    except forecast_model.NoForecastError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    output.mkdir(parents=True, exist_ok=True)
    (output / calibrate.REPORT).write_text(report.model_dump_json(indent=2))
    typer.echo(calibrate.summarise(report), nl=False)
    typer.echo(f"\nwrote {output / calibrate.REPORT}")


@app.command(name="calibration-report", help="Reliability curves and the summary, from a backtest.")
def calibration_report(
    output: Annotated[
        Path, typer.Option(help="Where backtest.json is and the plot goes.")
    ] = RESULTS,
) -> None:
    source = output / calibrate.REPORT
    if not source.exists():
        typer.echo(f"{source} is not there, run 'pitadv backtest' first", err=True)
        raise typer.Exit(1)
    report = forecast_model.Report.model_validate_json(source.read_text())
    try:
        figure = calibrate.render(report, output)
    except calibrate.MissingPlotterError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    summary = output / calibrate.SUMMARY
    summary.write_text(calibrate.summarise(report))
    typer.echo(calibrate.summarise(report), nl=False)
    typer.echo(f"\nwrote {figure} and {summary}")


@app.command(name="catalog-sync", help="Point the Glue catalog at the bronze tables.")
def catalog_sync(
    check: Annotated[
        bool, typer.Option("--check", help="Report drift without writing to Glue.")
    ] = False,
) -> None:
    settings = get_settings()
    glue = _client(boto_session(settings), "glue", settings.aws_region)
    actions = catalog.sync(glue, settings.glue_database, settings.data_bucket, apply=not check)
    for action in actions:
        suffix = f"  {action.detail}" if action.detail else ""
        typer.echo(f"{action.action:<9} {action.table}{suffix}")
    drifted = [action for action in actions if action.action != "unchanged"]
    if check and drifted:
        raise typer.Exit(1)


@app.command(name="lineage", help="Trace every gold model back to the raw it was built from.")
def lineage_command(
    check: Annotated[
        bool, typer.Option("--check", help="Exit non-zero on a broken trace.")
    ] = False,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    manifest: Annotated[Path, typer.Option(help="dbt manifest to read.")] = lineage.MANIFEST,
) -> None:
    store, _, _ = _runtime(local, get_settings())
    traces = lineage.trace(store, lineage.load(manifest))
    for item in traces:
        typer.echo(f"{'ok  ' if item.ok else 'FAIL'}  {item.model:<24} {item.detail}")
    if not traces:
        typer.echo("no gold models in the manifest")
        raise typer.Exit(1)
    if check and not all(item.ok for item in traces):
        raise typer.Exit(1)


EVAL_RESULTS = Path("results/evals")
GOLDEN = Path("evals/golden.yaml")


@app.command(help="Ask the grounded agent one question.")
def ask(
    question: Annotated[str, typer.Argument(help="What to ask.")],
    local: Annotated[bool, typer.Option("--local", help="Local lake and local corpus.")] = False,
    show_tools: Annotated[bool, typer.Option("--tools", help="Print the tool calls.")] = False,
) -> None:
    settings = get_settings()
    store, _, _ = _runtime(local, settings)
    answer = agent_for(settings, agent_tools.toolbox(settings, store, local)).ask(question)
    if show_tools:
        for call in answer.calls:
            marker = "ok  " if call.ok else "FAIL"
            typer.echo(
                f"{marker}  {call.name:<18} {call.arguments}{'' if call.ok else '  ' + call.detail}"
            )
        typer.echo("")
    typer.echo(answer.text)
    if answer.ungrounded:
        typer.echo(f"\nungrounded figures: {', '.join(answer.ungrounded)}", err=True)
        raise typer.Exit(1)


@app.command(help="Score the agent against the golden set.")
def evals(
    suite: Annotated[Path, typer.Option(help="The golden set to run.")] = GOLDEN,
    report: Annotated[Path, typer.Option(help="Where the artifacts land.")] = EVAL_RESULTS,
    local: Annotated[bool, typer.Option("--local", help="Local lake and local corpus.")] = False,
    only: Annotated[str | None, typer.Option(help="Comma separated case ids or kinds.")] = None,
    pace: Annotated[float, typer.Option(help="Seconds between cases, for the model quota.")] = 2.0,
) -> None:
    settings = get_settings()
    store, _, _ = _runtime(local, settings)
    try:
        loaded = agent_evals.load(suite)
    except agent_evals.SuiteError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    if only:
        wanted = {item.strip() for item in only.split(",") if item.strip()}
        cases = [case for case in loaded.cases if case.id in wanted or case.kind in wanted]
        if not cases:
            raise typer.BadParameter(f"nothing in the suite matches {only}")
        loaded = loaded.model_copy(update={"cases": cases})
    box = agent_tools.toolbox(settings, store, local)

    def announce(case: agent_evals.CaseScore) -> None:
        typer.echo(f"{'ok  ' if not agent_evals.failed(case) else 'FAIL'}  {case.id}")

    result = agent_evals.run(
        agent_for(settings, box, strict=False),
        box,
        loaded,
        _run_id(),
        pace_seconds=pace,
        on_case=announce,
    )
    report.mkdir(parents=True, exist_ok=True)
    (report / agent_evals.REPORT).write_text(result.model_dump_json(indent=2) + "\n")
    (report / agent_evals.SUMMARY).write_text(agent_evals.summarise(result))
    typer.echo(agent_evals.summarise(result), nl=False)
    typer.echo(f"\nwrote {report / agent_evals.REPORT}")
    if not result.passed:
        raise typer.Exit(1)


@app.command(name="docs-sync", help="Build the knowledge base corpus under docs/.")
def docs_sync(
    limit: Annotated[int | None, typer.Option(help="Stop after this many pages.")] = None,
    refresh: Annotated[
        bool, typer.Option("--refresh", help="Refetch pages already in the corpus.")
    ] = False,
    pause: Annotated[float, typer.Option(help="Seconds between pages, for the upstream.")] = 1.0,
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    index: Annotated[
        bool, typer.Option("--index", help="Reindex the corpus into the knowledge base.")
    ] = False,
    regulations: Annotated[
        bool, typer.Option("--regulations", help="Fetch the FIA regulations the manifest lists.")
    ] = False,
    add: Annotated[Path | None, typer.Option(help="Drop one curated file into the corpus.")] = None,
    title: Annotated[str | None, typer.Option(help="Title for the curated file.")] = None,
    kind: Annotated[str, typer.Option(help="Kind for the curated file.")] = "regulation",
    season: Annotated[int | None, typer.Option(help="Season the curated file covers.")] = None,
    issued: Annotated[
        str | None, typer.Option(help="Issue date of the curated file, YYYY-MM-DD.")
    ] = None,
) -> None:
    settings = get_settings()
    store, ledger, bucket_for = _runtime(local, settings)
    if add is not None:
        if title is None:
            raise typer.BadParameter("--add needs --title")
        item = doc_corpus.Curated(title=title, kind=kind, season=season, issued=_issued(issued))
        typer.echo(doc_corpus.add_curated(store, add, item))
        if index:
            _reindex(settings)
        return

    def announce(outcome: doc_corpus.DocOutcome) -> None:
        if outcome.skipped:
            typer.echo(f"skip  {outcome.title:<52} {outcome.skipped}")
        else:
            typer.echo(f"ok    {outcome.title:<52} {outcome.characters} characters")

    if regulations:
        outcomes = doc_corpus.ingest_regulations(
            store,
            RawStore(store),
            ledger,
            _run_id(),
            RateLimiter(bucket_for("fia_docs")),
            refresh=refresh,
            pause_seconds=pause if pause != 1.0 else doc_corpus.FIA_PAUSE_SECONDS,
            on_page=announce,
        )
        written = [item for item in outcomes if item.key]
        typer.echo(f"{len(written)} documents, {len(doc_corpus.corpus(store))} in the corpus")
        if index:
            _reindex(settings)
        return

    limiter = RateLimiter(bucket_for("wikipedia"))
    outcomes = doc_corpus.ingest_wikipedia(
        store,
        RawStore(store),
        ledger,
        _run_id(),
        limiter,
        limit=limit,
        refresh=refresh,
        pause_seconds=pause,
        on_page=announce,
    )
    written = [item for item in outcomes if item.key]
    typer.echo(f"{len(written)} documents, {len(doc_corpus.corpus(store))} in the corpus")
    if index:
        _reindex(settings)


def _issued(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{value} is not a date") from exc


def _reindex(settings: Settings) -> None:
    if not settings.knowledge_base_id or not settings.data_source_id:
        typer.echo(
            "PITADV_KNOWLEDGE_BASE_ID and PITADV_DATA_SOURCE_ID are not set, "
            "so there is no knowledge base to reindex",
            err=True,
        )
        raise typer.Exit(1)
    client = _client(boto_session(settings), "bedrock-agent", settings.aws_region)
    try:
        job = agent_kb.start_ingestion(client, settings.knowledge_base_id, settings.data_source_id)
    except agent_kb.IngestionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    typer.echo(
        f"{job.status.lower()}: {job.indexed} indexed of {job.scanned} scanned, {job.failed} failed"
    )
