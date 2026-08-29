import json
import os
import re
from importlib.metadata import version as package_version

import boto3.session
import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ProfileNotFound
from botocore.stub import Stubber
from typer.testing import CliRunner

from pitadvisor import cli
from pitadvisor.config import Settings
from pitadvisor.ingest.raw_store import LocalObjectStore
from pitadvisor.quality import catalog
from pitadvisor.types import EventKey

REGION = "ap-southeast-1"

# rich styles --flags mid-token, so the raw stream has escapes inside the option name
ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(text: str) -> str:
    return ANSI.sub("", text)


class FakeSession:
    def __init__(self, region):
        self.region_name = region
        self.clients = {}
        self.stubs = {}
        self.regions = {}
        self._boto = boto3.session.Session(
            aws_access_key_id="ak",
            aws_secret_access_key="sk",
            aws_session_token="tok",
            region_name=REGION,
        )

    def client(self, service, region_name=None):
        self.regions[service] = region_name
        if service not in self.clients:
            client = self._boto.client(service, region_name=region_name or REGION)
            stub = Stubber(client)
            stub.activate()
            self.clients[service] = client
            self.stubs[service] = stub
        return self.clients[service]

    def stub(self, service):
        self.client(service)
        return self.stubs[service]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    # the shell exports AWS_PROFILE=taskbuddy and settings read .env from cwd, keep both out
    for name in [n for n in os.environ if n.startswith(("AWS_", "PITADV_"))]:
        monkeypatch.delenv(name)
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def aws(monkeypatch, settings):
    session = FakeSession(settings.aws_region)
    monkeypatch.setattr(cli, "boto_session", lambda *_: session)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return session


def caller_identity(aws, account):
    aws.stub("sts").add_response(
        "get_caller_identity",
        {
            "UserId": "AIDAPITADVISOR",
            "Account": account,
            "Arn": f"arn:aws:iam::{account}:user/pitadvisor-dev",
        },
        {},
    )


def work_group(aws, settings, configuration):
    aws.stub("athena").add_response(
        "get_work_group",
        {"WorkGroup": {"Name": settings.athena_workgroup, "Configuration": configuration}},
        {"WorkGroup": settings.athena_workgroup},
    )


def stub_healthy(aws, settings, skip=None):
    healthy = {
        "sts": (
            "get_caller_identity",
            {
                "UserId": "AIDAPITADVISOR",
                "Account": settings.account_id,
                "Arn": f"arn:aws:iam::{settings.account_id}:user/pitadvisor-dev",
            },
            {},
        ),
        "s3": ("head_bucket", {}, {"Bucket": settings.data_bucket}),
        "glue": (
            "get_database",
            {"Database": {"Name": settings.glue_database}},
            {"Name": settings.glue_database},
        ),
        "athena": (
            "get_work_group",
            {
                "WorkGroup": {
                    "Name": settings.athena_workgroup,
                    "Configuration": {"BytesScannedCutoffPerQuery": settings.max_scanned_bytes},
                }
            },
            {"WorkGroup": settings.athena_workgroup},
        ),
        "budgets": (
            "describe_budget",
            {
                "Budget": {
                    "BudgetName": settings.budget_name,
                    "BudgetLimit": {"Amount": "20", "Unit": "USD"},
                    "TimeUnit": "MONTHLY",
                    "BudgetType": "COST",
                }
            },
            {"AccountId": settings.account_id, "BudgetName": settings.budget_name},
        ),
    }
    for service, (method, response, params) in healthy.items():
        if service != skip:
            aws.stub(service).add_response(method, response, params)
    if skip != "budgets":
        subscribed_budget(aws, settings)


def subscribed_budget(aws, settings):
    aws.stub("budgets").add_response(
        "describe_notifications_for_budget",
        {
            "Notifications": [
                {
                    "NotificationType": "ACTUAL",
                    "ComparisonOperator": "GREATER_THAN",
                    "Threshold": 80.0,
                }
            ]
        },
        {"AccountId": settings.account_id, "BudgetName": settings.budget_name},
    )


def raises(exc):
    def raise_it(*_args, **_kwargs):
        raise exc

    return raise_it


BROKEN = [
    ("sts", "get_caller_identity", "credentials", "ExpiredToken"),
    ("s3", "head_bucket", "data bucket", "403"),
    ("glue", "get_database", "glue database", "EntityNotFoundException"),
    ("athena", "get_work_group", "athena workgroup", "InvalidRequestException"),
    ("budgets", "describe_budget", "budget", "NotFoundException"),
]


def test_credentials_accepts_the_project_account(aws, settings):
    caller_identity(aws, settings.account_id)
    result = cli.check_credentials(aws, settings)
    assert result.ok
    assert settings.account_id in result.detail


def test_credentials_rejects_another_account(aws, settings):
    caller_identity(aws, "999988887777")
    result = cli.check_credentials(aws, settings)
    assert not result.ok
    assert "999988887777" in result.detail
    assert settings.account_id in result.detail


def test_region_matches(aws, settings):
    result = cli.check_region(aws, settings)
    assert result.ok
    assert result.detail == settings.aws_region


@pytest.mark.parametrize(("region", "shown"), [("us-east-1", "us-east-1"), (None, "unset")])
def test_region_mismatch(aws, settings, region, shown):
    aws.region_name = region
    result = cli.check_region(aws, settings)
    assert not result.ok
    assert result.detail == f"{shown}, expected {settings.aws_region}"


def test_athena_workgroup_without_a_scan_cap(aws, settings):
    work_group(aws, settings, {"EnforceWorkGroupConfiguration": True})
    result = cli.check_athena_workgroup(aws, settings)
    assert not result.ok
    assert "no per-query scan cap" in result.detail


def test_athena_workgroup_detail_carries_the_cap(aws, settings):
    work_group(aws, settings, {"BytesScannedCutoffPerQuery": settings.max_scanned_bytes})
    result = cli.check_athena_workgroup(aws, settings)
    assert result.ok
    assert str(settings.max_scanned_bytes) in result.detail


def test_athena_workgroup_cap_above_the_configured_limit(aws, settings):
    work_group(aws, settings, {"BytesScannedCutoffPerQuery": settings.max_scanned_bytes * 10})
    result = cli.check_athena_workgroup(aws, settings)
    assert not result.ok
    assert "above the configured" in result.detail


def test_budget_without_subscribers_fails(aws, settings):
    aws.stub("budgets").add_response(
        "describe_budget",
        {
            "Budget": {
                "BudgetName": settings.budget_name,
                "BudgetLimit": {"Amount": "20", "Unit": "USD"},
                "TimeUnit": "MONTHLY",
                "BudgetType": "COST",
            }
        },
        {"AccountId": settings.account_id, "BudgetName": settings.budget_name},
    )
    aws.stub("budgets").add_response(
        "describe_notifications_for_budget",
        {"Notifications": []},
        {"AccountId": settings.account_id, "BudgetName": settings.budget_name},
    )
    result = cli.check_budget(aws, settings)
    assert not result.ok
    assert "nobody is subscribed" in result.detail


@pytest.mark.parametrize(
    ("exc", "detail"),
    [
        (ClientError({"Error": {"Code": "AccessDenied"}}, "HeadBucket"), "AccessDenied"),
        (ClientError({"Error": {}}, "HeadBucket"), "ClientError"),
        (NoCredentialsError(), "NoCredentialsError"),
        (RuntimeError("boom"), "RuntimeError: boom"),
    ],
)
def test_detail(exc, detail):
    assert cli._detail(exc) == detail


def test_run_checks_all_green(aws, settings):
    stub_healthy(aws, settings)
    results = cli.run_checks(settings)
    assert [r.name for r in results] == [name for name, _ in cli.CHECKS]
    assert all(r.ok for r in results)
    assert aws.regions["budgets"] == "us-east-1"
    assert aws.regions["sts"] is None


@pytest.mark.parametrize(("service", "method", "name", "code"), BROKEN)
def test_a_client_error_becomes_a_failed_check(aws, settings, service, method, name, code):
    stub_healthy(aws, settings, skip=service)
    aws.stub(service).add_client_error(method, service_error_code=code)
    results = cli.run_checks(settings)
    failed = [r for r in results if not r.ok]
    assert [r.name for r in failed] == [name]
    assert failed[0].detail == code


def test_doctor_all_green(runner, aws, settings):
    stub_healthy(aws, settings)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "FAIL" not in result.output
    for name, _ in cli.CHECKS:
        assert name in result.output
    assert f"{settings.budget_name} at 20 USD" in result.output


@pytest.mark.parametrize(("service", "method", "name", "code"), BROKEN)
def test_doctor_exits_one_when_a_check_fails(runner, aws, settings, service, method, name, code):
    stub_healthy(aws, settings, skip=service)
    aws.stub(service).add_client_error(method, service_error_code=code)
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert [item["name"] for item in payload if not item["ok"]] == [name]
    assert next(item["detail"] for item in payload if item["name"] == name) == code


def test_doctor_reports_a_wrong_region(runner, aws, settings):
    aws.region_name = "us-east-1"
    stub_healthy(aws, settings)
    result = runner.invoke(cli.app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    assert f"us-east-1, expected {settings.aws_region}" in result.output


def test_doctor_json(runner, aws, settings):
    stub_healthy(aws, settings)
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert [item["name"] for item in payload] == [name for name, _ in cli.CHECKS]
    assert all(set(item) == {"name", "ok", "detail"} for item in payload)
    assert all(item["ok"] and item["detail"] for item in payload)


@pytest.mark.parametrize("exc", [NoCredentialsError(), ProfileNotFound(profile="pitadvisor")])
def test_doctor_survives_missing_credentials(runner, monkeypatch, settings, exc):
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "boto_session", raises(exc))
    result = runner.invoke(cli.app, ["doctor", "--json"])
    assert result.exit_code == 1
    assert json.loads(result.output) == [
        {"name": "credentials", "ok": False, "detail": type(exc).__name__}
    ]


def test_version(runner):
    result = runner.invoke(cli.app, ["version"])
    assert result.exit_code == 0
    assert result.output.strip() == package_version("pitadvisor")


@pytest.fixture
def lake(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    return tmp_path / "data" / "local"


@pytest.fixture
def offline(monkeypatch, fetch):
    monkeypatch.setattr("pitadvisor.ingest.http.fetch", fetch)
    return fetch


def test_dry_run_asks_for_nothing(lake, offline):
    result = CliRunner().invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--dry-run"]
    )
    assert result.exit_code == 0
    assert "GET https://api.jolpi.ca/ergast/f1/2024.json" in result.stdout
    assert offline.calls == []


def test_dry_run_counts_the_requests_against_the_cap(lake, offline):
    result = CliRunner().invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--dry-run"]
    )
    assert "200/hour cap" in result.stdout


def test_ingest_local_writes_bronze(lake, offline):
    result = CliRunner().invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    assert result.exit_code == 0, result.stdout
    assert (lake / "bronze/table=results/season=2024/round=05/results.parquet").is_file()
    assert "3 rows" in result.stdout


def test_ingest_local_persists_the_quota_between_runs(lake, offline):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    spent = json.loads((lake / "quota.json").read_text())["jolpica"]["tokens"]
    assert spent < 200


def test_weather_needs_the_schedule_first(lake, offline):
    result = CliRunner().invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--local"]
    )
    assert result.exit_code != 0
    assert "ingest jolpica races first" in plain(result.stderr)


def test_weather_follows_the_schedule(lake, offline):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    result = runner.invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--local"]
    )
    assert result.exit_code == 0, result.stdout
    assert (lake / "bronze/table=weather/season=2024/round=05/weather.parquet").is_file()


def test_fastf1_needs_a_session(lake, offline):
    result = CliRunner().invoke(
        cli.app, ["ingest", "--source", "fastf1", "--season", "2024", "--round", "5", "--local"]
    )
    assert result.exit_code != 0
    assert "--round and --session" in plain(result.stderr)


def test_backfill_resumes_without_refetching(lake, offline):
    runner = CliRunner()
    first = runner.invoke(cli.app, ["backfill", "--from", "2024", "--to", "2024", "--local"])
    assert first.exit_code == 0, first.stdout
    calls = len(offline.calls)
    second = runner.invoke(cli.app, ["backfill", "--from", "2024", "--to", "2024", "--local"])
    assert "already in bronze" in second.stdout
    assert len(offline.calls) == calls + 1


def test_quality_report_fails_on_an_empty_lake(lake, offline):
    result = CliRunner().invoke(cli.app, ["quality-report", "--local"])
    assert result.exit_code == 1
    assert "is empty" in result.stdout


def test_quality_report_passes_after_an_ingest(lake, offline):
    runner = CliRunner()
    runner.invoke(cli.app, ["backfill", "--from", "2024", "--to", "2024", "--local"])
    result = runner.invoke(cli.app, ["quality-report", "--local"])
    assert result.exit_code == 0, result.stdout
    assert "row_count" in result.stdout


def test_quality_report_json(lake, offline):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    result = runner.invoke(cli.app, ["quality-report", "--local", "--json"])
    assert json.loads(result.stdout)["layer"] == "bronze"


def test_emit_views_writes_the_pipeline_view(lake, offline):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    result = runner.invoke(cli.app, ["emit-views", "--local"])
    assert result.exit_code == 0, result.stdout
    view = json.loads((lake / "views/pipeline_view.json").read_text())
    assert view["view"] == "pipeline_view"
    assert view["quota"][0]["name"] == "jolpica"


def test_emit_views_rejects_an_unknown_view(lake, offline):
    result = CliRunner().invoke(cli.app, ["emit-views", "--views", "standings", "--local"])
    assert result.exit_code != 0
    assert "no emitter yet" in plain(result.stderr)


def manifest_at(path, sources=("results", "races")):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "nodes": {
                    "model.pitadvisor.gold_race_results": {
                        "resource_type": "model",
                        "name": "gold_race_results",
                        "tags": ["gold"],
                        "depends_on": {
                            "nodes": [f"source.pitadvisor.bronze.{name}" for name in sources]
                        },
                    }
                },
                "sources": {f"source.pitadvisor.bronze.{name}": {"name": name} for name in sources},
            }
        )
    )
    return path


def test_lineage_traces_gold_back_to_raw(lake, offline, tmp_path):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    manifest = manifest_at(tmp_path / "transform" / "target" / "manifest.json")
    result = runner.invoke(cli.app, ["lineage", "--check", "--local", "--manifest", str(manifest)])
    assert result.exit_code == 0, result.stdout
    assert "gold_race_results" in result.stdout


def test_lineage_fails_when_a_source_never_landed(lake, offline, tmp_path):
    manifest = manifest_at(tmp_path / "transform" / "target" / "manifest.json")
    result = CliRunner().invoke(
        cli.app, ["lineage", "--check", "--local", "--manifest", str(manifest)]
    )
    assert result.exit_code == 1
    assert "nothing in raw/" in result.stdout


def test_catalog_sync_creates_the_bronze_tables(aws, settings):
    for _ in range(len(catalog.TABLES)):
        aws.stub("glue").add_client_error("get_table", "EntityNotFoundException")
        aws.stub("glue").add_response("create_table", {}, None)
    result = CliRunner().invoke(cli.app, ["catalog-sync"])
    assert result.exit_code == 0, result.stdout
    assert "create    results" in result.stdout


def test_catalog_sync_check_reports_drift_without_writing(aws, settings):
    for _ in range(7):
        aws.stub("glue").add_client_error("get_table", "EntityNotFoundException")
    result = CliRunner().invoke(cli.app, ["catalog-sync", "--check"])
    assert result.exit_code == 1


def test_ingest_leaves_the_lap_times_alone_by_default(lake, offline):
    CliRunner().invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    assert not any("/laps.json" in url for url in offline.calls)
    assert any("/results.json" in url for url in offline.calls)


def test_ingest_asks_for_laps_when_told_to(lake, offline):
    CliRunner().invoke(
        cli.app,
        [
            "ingest",
            "--source",
            "jolpica",
            "--season",
            "2024",
            "--round",
            "5",
            "--with-laps",
            "--local",
        ],
    )
    assert any("/laps.json" in url for url in offline.calls)


def test_rebuild_replays_bronze_without_asking_upstream(lake, offline):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    spent = len(offline.calls)
    for parquet in (lake / "bronze").rglob("*.parquet"):
        parquet.unlink()

    result = runner.invoke(cli.app, ["rebuild", "--local"])

    assert result.exit_code == 0, result.stdout
    assert len(offline.calls) == spent
    assert (lake / "bronze/table=results/season=2024/round=05/results.parquet").is_file()
    assert "0 requests" in result.stdout


def test_rebuild_only_knows_how_to_make_bronze(lake, offline):
    result = CliRunner().invoke(cli.app, ["rebuild", "--layer", "silver", "--local"])
    assert result.exit_code != 0
    assert "only bronze replays from raw" in plain(result.stderr)


def test_rebuild_says_so_when_raw_is_empty(lake, offline):
    result = CliRunner().invoke(cli.app, ["rebuild", "--local"])
    assert result.exit_code == 0
    assert "nothing in raw matched" in result.stdout


def seeded_local(lake, seed_lake, **kwargs):
    return seed_lake(LocalObjectStore(lake), **kwargs)


def test_metrics_reports_what_the_fit_stands_on(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(cli.app, ["metrics", "--event", "2024:6", "--local"])
    assert result.exit_code == 0, result.stdout
    assert "suzuka" in result.stdout
    assert "form" in result.stdout
    assert "dnf" in result.stdout


def test_metrics_explain_prints_the_exclusion_reasons(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(cli.app, ["metrics", "--event", "2024:6", "--explain", "--local"])
    assert result.exit_code == 0, result.stdout
    assert "exclusions" in result.stdout
    assert "opening_laps" in result.stdout


def test_metrics_without_explain_keeps_the_reasons_out(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(cli.app, ["metrics", "--event", "2024:6", "--local"])
    assert "opening_laps" not in result.stdout


def test_metrics_rejects_an_event_it_cannot_parse(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(cli.app, ["metrics", "--event", "suzuka", "--local"])
    assert result.exit_code != 0
    assert "season:round" in plain(result.stderr)


def test_emit_views_writes_the_three_event_views(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(
        cli.app,
        ["emit-views", "--views", "weekend,driver,track", "--event", "2024:6", "--local"],
    )
    assert result.exit_code == 0, result.stdout
    for name in ("weekend", "driver", "track"):
        payload = json.loads((lake / f"views/{name}_view.json").read_text())
        assert payload["view"] == f"{name}_view"
        assert payload["event"]["circuit_id"] == "suzuka"


def test_emit_views_assembles_once_for_every_event_view(lake, seed_lake, monkeypatch):
    seeded_local(lake, seed_lake)
    calls = []
    original = cli.feature_assemble.assemble

    def counted(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(cli.feature_assemble, "assemble", counted)
    result = CliRunner().invoke(
        cli.app,
        ["emit-views", "--views", "weekend,driver,track", "--event", "2024:6", "--local"],
    )
    assert result.exit_code == 0, result.stdout
    assert len(calls) == 1


def test_metrics_says_what_is_missing_when_the_lake_has_no_session_laps(lake, offline):
    result = CliRunner().invoke(cli.app, ["ingest", "--source", "jolpica", "--local"])
    assert result.exit_code == 0, result.stdout
    result = CliRunner().invoke(cli.app, ["metrics", "--local"])
    assert result.exit_code == 1
    assert "no fastf1 session_laps" in plain(result.stderr)


def test_backtest_scores_the_simulation_and_writes_the_report(lake, seed_lake, tmp_path):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(
        cli.app,
        ["backtest", "--from", "2024", "--holdout", "3", "--paths", "150", "--local"],
    )
    assert result.exit_code == 0, result.stdout
    written = json.loads((tmp_path / "results" / "backtest" / "backtest.json").read_text())
    assert {item["name"] for item in written["scored"]} >= {"simulation", "grid"}
    assert "log loss" in result.stdout


def test_backtest_with_nothing_to_hold_out_exits_non_zero(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(
        cli.app, ["backtest", "--from", "2099", "--holdout", "5", "--local"]
    )
    assert result.exit_code == 1
    assert "hold out" in plain(result.stderr)


def test_calibration_report_needs_a_backtest_first(lake, seed_lake):
    seeded_local(lake, seed_lake)
    result = CliRunner().invoke(cli.app, ["calibration-report"])
    assert result.exit_code == 1
    assert "run 'pitadv backtest' first" in plain(result.stderr)


def test_calibration_report_draws_the_curve_and_writes_the_summary(lake, seed_lake, tmp_path):
    seeded_local(lake, seed_lake)
    CliRunner().invoke(
        cli.app,
        ["backtest", "--from", "2024", "--holdout", "3", "--paths", "150", "--local"],
    )
    result = CliRunner().invoke(cli.app, ["calibration-report"])
    assert result.exit_code == 0, result.stdout
    output = tmp_path / "results" / "backtest"
    assert (output / "reliability.png").stat().st_size > 5_000
    assert "log loss" in (output / "summary.txt").read_text()


def test_emit_views_writes_the_forecast_and_the_calibration_view(lake, seed_lake, tmp_path):
    seeded_local(lake, seed_lake)
    CliRunner().invoke(
        cli.app,
        ["backtest", "--from", "2024", "--holdout", "3", "--paths", "150", "--local"],
    )
    result = CliRunner().invoke(
        cli.app,
        ["emit-views", "--views", "forecast,calibration", "--paths", "200", "--local"],
    )
    assert result.exit_code == 0, result.stdout
    forecast = json.loads((lake / "views" / "forecast_view.json").read_text())
    calibration = json.loads((lake / "views" / "calibration_view.json").read_text())
    assert forecast["view"] == "forecast_view"
    assert forecast["evidence"]["beats_baselines"] in (True, False)
    assert calibration["view"] == "calibration_view"
    assert calibration["per_race"]


def test_the_forecast_view_does_not_read_an_archive_as_a_forecast(lake, seed_lake):
    """Archived weather says what happened. Only a real forecast is allowed to weight the
    scenarios, or the simulation would know whether it rained before predicting the race."""
    from datetime import UTC, datetime, timedelta

    from pitadvisor.ingest.raw_store import write_bronze
    from pitadvisor.quality.contracts import WeatherRow

    built = seeded_local(lake, seed_lake)
    store = LocalObjectStore(lake)
    key = EventKey(season=2024, round=6)
    start = datetime.combine(built.held(2024, 6), datetime.min.time(), tzinfo=UTC).replace(hour=13)
    write_bronze(
        store,
        "weather",
        key,
        [
            WeatherRow(
                run_id="run-1",
                ingested_at=datetime(2025, 1, 1, tzinfo=UTC),
                season=2024,
                round=6,
                circuit_id="suzuka",
                observed_at=start + timedelta(hours=hour),
                is_forecast=False,
                temperature_c=22.0,
                precipitation_mm=0.0,
                precipitation_probability=None,
                wind_speed_kph=8.0,
                relative_humidity=55.0,
            )
            for hour in range(3)
        ],
    )
    result = CliRunner().invoke(
        cli.app, ["emit-views", "--views", "forecast", "--paths", "150", "--local"]
    )
    assert result.exit_code == 0, result.stdout
    view = json.loads((lake / "views" / "forecast_view.json").read_text())
    assert view["weights_are_forecast"] is False
