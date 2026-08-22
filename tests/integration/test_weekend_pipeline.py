import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from pitadvisor import cli

REPO = Path(__file__).resolve().parents[2]
DBT = shutil.which("dbt")

pytestmark = pytest.mark.skipif(DBT is None, reason="dbt is in the transform group")


@pytest.fixture
def lake(monkeypatch, tmp_path, fetch):
    for name in [n for n in os.environ if n.startswith(("AWS_", "PITADV_"))]:
        monkeypatch.delenv(name)
    monkeypatch.setattr("pitadvisor.ingest.http.fetch", fetch)
    monkeypatch.chdir(tmp_path)
    return tmp_path / "data" / "local"


def dbt(tmp_path, lake, *args):
    return subprocess.run(
        [
            str(DBT),
            *args,
            "--project-dir",
            str(REPO / "transform"),
            "--target",
            "local",
            "--target-path",
            str(tmp_path / "target"),
            "--log-path",
            str(tmp_path / "logs"),
        ],
        cwd=REPO,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PITADV_LOCAL_LAKE": str(lake),
            "PITADV_DUCKDB": str(tmp_path / "pipeline.duckdb"),
        },
    )


def test_a_weekend_goes_from_upstream_json_to_a_gold_mart(lake, tmp_path):
    runner = CliRunner()
    ingested = runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    assert ingested.exit_code == 0, ingested.stdout
    weather = runner.invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--round", "5", "--local"]
    )
    assert weather.exit_code == 0, weather.stdout

    quality = runner.invoke(cli.app, ["quality-report", "--layer", "bronze", "--local"])
    assert quality.exit_code == 0, quality.stdout

    built = dbt(tmp_path, lake, "build")
    assert built.returncode == 0, built.stdout

    traced = runner.invoke(
        cli.app,
        [
            "lineage",
            "--check",
            "--local",
            "--manifest",
            str(tmp_path / "target" / "manifest.json"),
        ],
    )
    assert traced.exit_code == 0, traced.stdout
    assert "gold_race_results" in traced.stdout

    connection = duckdb.connect(str(tmp_path / "pipeline.duckdb"), read_only=True)
    silver = connection.execute("select count(*) from silver_results").fetchone()
    gold = connection.execute("select count(*) from gold_race_results").fetchone()
    assert silver == gold
    assert gold != (0,)


def test_an_amended_result_replaces_the_old_row(lake, tmp_path):
    runner = CliRunner()
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    runner.invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--round", "5", "--local"]
    )
    assert dbt(tmp_path, lake, "run").returncode == 0

    # the same event lands again, as it does when a penalty is applied after the race
    runner.invoke(
        cli.app, ["ingest", "--source", "jolpica", "--season", "2024", "--round", "5", "--local"]
    )
    rebuilt = dbt(tmp_path, lake, "run")
    assert rebuilt.returncode == 0, rebuilt.stdout

    connection = duckdb.connect(str(tmp_path / "pipeline.duckdb"), read_only=True)
    duplicated = connection.execute(
        "select count(*) - count(distinct result_id) from silver_results"
    ).fetchone()
    assert duplicated == (0,)
