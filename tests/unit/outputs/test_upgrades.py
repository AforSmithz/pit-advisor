from datetime import date

import polars as pl

from pitadvisor.features.assemble import EventContext
from pitadvisor.outputs import upgrades

CONTEXT = EventContext(
    season=2026, round=3, circuit_id="x", race_name="Test GP", race_date=date(2026, 3, 9)
)
RACES = pl.DataFrame({"season": [2026] * 4, "round": [1, 2, 3, 4]})
UPDATES = pl.DataFrame(
    {
        "season": [2026] * 5,
        "round": [1, 1, 2, 3, 3],
        "constructor_id": ["haas", "haas", "haas", "haas", "ferrari"],
        "ordinal": [1, 2, 0, 1, 0],
        "category": ["performance", "circuit", "none", "reliability", "none"],
        "text": ["Floor  body", "Brake duct", "No updates", "Exit duct", "No updates"],
    }
)


def test_each_team_has_a_cell_for_every_round_up_to_this_one():
    view = upgrades.upgrades_view(UPDATES, RACES, CONTEXT, "run-1")
    assert view.rounds == [1, 2, 3]
    haas = next(item for item in view.teams if item.constructor_id == "haas")
    assert [cell.read for cell in haas.rounds] == [True, True, True]
    assert haas.rounds[0].parts["performance"] == 1
    assert haas.rounds[0].parts["circuit"] == 1
    assert sum(haas.rounds[1].parts.values()) == 0


def test_a_round_with_no_document_read_is_not_read_as_no_upgrades():
    view = upgrades.upgrades_view(UPDATES, RACES, CONTEXT, "run-1")
    ferrari = next(item for item in view.teams if item.constructor_id == "ferrari")
    assert [cell.read for cell in ferrari.rounds] == [False, False, True]


def test_this_events_declarations_are_listed_with_their_text():
    view = upgrades.upgrades_view(UPDATES, RACES, CONTEXT, "run-1")
    assert [(item.constructor_id, item.category, item.text) for item in view.this_event] == [
        ("haas", "reliability", "Exit duct")
    ]
