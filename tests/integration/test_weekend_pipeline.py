import os
import shutil
import subprocess
from pathlib import Path

import duckdb
import pytest
from typer.testing import CliRunner

from pitadvisor import cli
from pitadvisor.types import Source

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


def stewards_document(lake):
    """One published decision, landed the way the crawler lands it and already read, so the
    replay reaches bronze without opening a pdf and the incident models have something to
    build from."""
    from datetime import UTC, datetime

    from pitadvisor.incidents import lake as incident_lake
    from pitadvisor.incidents.parse import Article, Book, Decision
    from pitadvisor.ingest.raw_store import LocalObjectStore, RawStore
    from pitadvisor.types import EventKey, Provenance

    store = LocalObjectStore(lake)
    RawStore(store).land(
        EventKey(season=2024, round=5),
        "20240505T1800-decision-car-7-collision",
        b"%PDF-1.4 stub",
        Provenance(
            run_id="run-1",
            source=Source.FIA_DOCS,
            url="https://www.fia.com/decision.pdf",
            fetched_at=datetime(2024, 5, 5, 18, tzinfo=UTC),
            status=200,
        ),
        suffix="pdf",
    )
    key = next(item.key for item in store.list("raw/source=fia_docs/") if item.key.endswith(".pdf"))
    store.put(
        incident_lake.cache_key(key),
        incident_lake.dump(
            incident_lake.Reading(
                raw_key=key,
                kind="decision",
                read_by=incident_lake.EXTRACTED,
                decisions=[
                    Decision(
                        document=41,
                        car=7,
                        driver="Jo Mercier",
                        session="Race",
                        charge=(
                            "Breach of Article 33.4 of the FIA Formula One Sporting Regulations."
                        ),
                        articles=[
                            Article(
                                code="Article 33.4",
                                regulation="FIA Formula One Sporting Regulations",
                                book=Book.SPORTING,
                            )
                        ],
                        outcome="10 second time penalty.",
                    )
                ],
            )
        ),
    )
    replayed = CliRunner().invoke(cli.app, ["rebuild", "--source", "fia_docs", "--local"])
    assert replayed.exit_code == 0, replayed.output


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
    assert ingested.exit_code == 0, ingested.stdout
    weather = runner.invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--round", "5", "--local"]
    )
    assert weather.exit_code == 0, weather.stdout

    quality = runner.invoke(cli.app, ["quality-report", "--layer", "bronze", "--local"])
    assert quality.exit_code == 0, quality.stdout
    stewards_document(lake)

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
    runner.invoke(
        cli.app, ["ingest", "--source", "open_meteo", "--season", "2024", "--round", "5", "--local"]
    )
    stewards_document(lake)
    assert dbt(tmp_path, lake, "run").returncode == 0

    # the same event lands again, as it does when a penalty is applied after the race
    runner.invoke(
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
    rebuilt = dbt(tmp_path, lake, "run")
    assert rebuilt.returncode == 0, rebuilt.stdout

    connection = duckdb.connect(str(tmp_path / "pipeline.duckdb"), read_only=True)
    duplicated = connection.execute(
        "select count(*) - count(distinct result_id) from silver_results"
    ).fetchone()
    assert duplicated == (0,)
