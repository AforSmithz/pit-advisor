from datetime import UTC, datetime

from pitadvisor.ingest.raw_store import LocalObjectStore
from pitadvisor.outputs import lineage
from pitadvisor.quality.checks import Outcome, QualityReport, Status
from pitadvisor.types import Layer


def test_every_view_input_is_traced_with_its_gate_verdict(tmp_path):
    report = QualityReport(
        layer=Layer.BRONZE,
        generated_at=datetime(2026, 10, 1, tzinfo=UTC),
        outcomes=[
            Outcome(check="row_count", table="results", status=Status.OK, detail="10 rows"),
            Outcome(check="freshness", table="results", status=Status.WARN, detail="200h old"),
        ],
        quarantine=[],
    )
    view = lineage.lineage_view(LocalObjectStore(tmp_path), report, "run-1")
    assert set(view.views) == set(lineage.INPUTS)
    results = next(item for item in view.tables if item.table == "results")
    assert (results.status, results.rows, results.freshness) == ("warn", "10 rows", "200h old")
    absent = next(item for item in view.tables if item.table == "car_updates")
    assert absent.status == "absent"
    every = {table for tables in view.views.values() for table in tables}
    assert {item.table for item in view.tables} == every
