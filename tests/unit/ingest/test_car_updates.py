from datetime import datetime

import polars as pl

from pitadvisor.ingest import car_updates
from pitadvisor.ingest.raw_store import LocalObjectStore
from pitadvisor.quality.checks import read_table
from pitadvisor.types import EventKey, Layer

# shaped like pypdf's text of a real submission: non-breaking spaces in the header date, the
# table columns flattened into running text, and reasons wrapped across lines
DOCUMENT = (
    "2024 AUSTRIAN GRAND PRIX\n28 - 30 June 2024\nDocument 6\n"
    "Date 28\xa0June\xa02024\nTime 09:56\nTitle Car\xa0Presentation Submissions\n"
    "Car Presentation \u2013 Austrian Grand Prix \n"
    "ORACLE RED BULL RACING  \n \nNo updates submitted for this event. \n \n"
    "MCLAREN FORMULA 1 TEAM \n  Updated \ncomponent \nPrimary reason \nfor update \n"
    "1 Front Wing Performance - \nFlow Conditioning New Front Wing geometry \n"
    "The new Front Wing geometry improves flow conditioning. \n"
    "2 Front \nSuspension \nPerformance - \nFlow Conditioning Updated Front Suspension \n"
    "SCUDERIA FERRARI \n  Updated \ncomponent \n"
    "1 Cooling \nLouvres \nCircuit specific - \nCooling Range Addition of extra cooling louvres \n"
    "VISA CASH APP RB FORMULA ONE TEAM \n"
    "1 Rear Corner Performance - \nFlow Conditioning winglets on the rear drum revised. \n"
)


def test_every_team_section_is_read_with_its_declared_updates():
    rows = car_updates.parse(DOCUMENT)
    found = {(row["constructor_id"], row["ordinal"], row["category"]) for row in rows}
    assert found == {
        ("red_bull", 0, "none"),
        ("mclaren", 1, "performance"),
        ("mclaren", 2, "performance"),
        ("ferrari", 1, "circuit"),
        ("rb", 1, "performance"),
    }


def test_every_field_is_a_literal_substring_of_the_document():
    for row in car_updates.parse(DOCUMENT):
        for name in ("team", "reason", "text"):
            assert row[name] in DOCUMENT


def test_racing_bulls_is_not_read_as_red_bull():
    assert car_updates.team_of("VISA CASH APP RACING BULLS F1 TEAM") == "rb"
    assert car_updates.team_of("ORACLE RED BULL RACING") == "red_bull"
    assert car_updates.team_of("Stake F1 Team Kick Sauber") is None
    assert car_updates.team_of("STAKE F1 TEAM KICK SAUBER") == "sauber"


def test_the_issue_time_survives_non_breaking_spaces():
    assert car_updates._issued(DOCUMENT) == datetime(2024, 6, 28, 9, 56)


def test_a_document_lands_in_bronze_through_the_contract(tmp_path):
    store = LocalObjectStore(tmp_path)
    stamp = {"run_id": "run-1", "ingested_at": datetime(2026, 10, 10)}
    kept, dropped = car_updates.materialize(
        store, EventKey(season=2024, round=11), "raw/x.pdf", "doc", DOCUMENT, stamp
    )
    assert (kept, dropped) == (5, 0)
    frame = read_table(store, Layer.BRONZE, "car_updates")
    assert frame is not None
    assert frame.filter(pl.col("category") == "performance").height == 3
