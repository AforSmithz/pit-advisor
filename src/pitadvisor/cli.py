import json
from collections.abc import Callable
from importlib.metadata import version as package_version
from typing import Annotated, Any, cast

import typer
from boto3.session import Session
from botocore.exceptions import BotoCoreError, ClientError
from pydantic import BaseModel

from pitadvisor.config import Settings, boto_session, get_settings

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
