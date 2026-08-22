import json
from collections.abc import Callable
from datetime import UTC, datetime
from importlib.metadata import version as package_version
from pathlib import Path
from typing import Annotated, Any, cast
from uuid import uuid4

import typer
from boto3.session import Session
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from pitadvisor.config import Settings, boto_session, get_settings
from pitadvisor.ingest import jolpica
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
from pitadvisor.ingest.weather import Circuit, WeatherClient, event_circuits
from pitadvisor.ingest.weather import ingest_event as weather_ingest_event
from pitadvisor.outputs.view_contracts import emit, pipeline_view
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
        if round_ is None:
            outcomes = jolpica.backfill(client, store, season)
        else:
            outcomes = jolpica.ingest_event(client, store, EventKey(season=season, round=round_))
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
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
    with_laps: Annotated[
        bool, typer.Option("--with-laps", help="Include per-lap timings.")
    ] = False,
) -> None:
    settings = get_settings()
    store, ledger, bucket_for = _runtime(local, settings)
    limiter = RateLimiter(bucket_for("jolpica"))
    client = jolpica.JolpicaClient(RawStore(store), ledger, limiter, _run_id())
    resources = (
        jolpica.RESOURCES if with_laps else tuple(r for r in jolpica.RESOURCES if r != "laps")
    )
    done: list[IngestOutcome] = []
    for season in range(from_, to + 1):
        try:
            done.extend(jolpica.backfill(client, store, season, resources))
        except QuotaExhaustedError as exc:
            _render_outcomes(done)
            typer.echo(f"stopped in {season}: {exc}. rerun to resume, raw and bronze are kept")
            raise typer.Exit(0) from exc
    _render_outcomes(done)


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
    if not result.ok:
        raise typer.Exit(1)


@app.command(name="emit-views", help="Write the versioned view artifacts the dashboard reads.")
def emit_views(
    views: Annotated[str, typer.Option(help="Comma separated view names.")] = "pipeline",
    local: Annotated[bool, typer.Option("--local", help="Local filesystem, no AWS.")] = False,
) -> None:
    settings = get_settings()
    store, _, bucket_for = _runtime(local, settings)
    wanted = {name.strip() for name in views.split(",") if name.strip()}
    unknown = wanted - {"pipeline"}
    if unknown:
        raise typer.BadParameter(f"no emitter yet for {', '.join(sorted(unknown))}")
    report = checks.report(store, Layer.BRONZE)
    quota = [bucket_for(name).state() for name in HOURLY_CAPS]
    view = pipeline_view(report, _run_id(), quota)
    typer.echo(emit(store, view))


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
