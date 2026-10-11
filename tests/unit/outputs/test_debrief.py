import dataclasses

import polars as pl

from pitadvisor.features.assemble import event_at
from pitadvisor.outputs import debrief, published
from tests.unit.outputs.test_published import issued, no_sprints


def test_a_run_race_has_its_call_its_finish_and_its_score(seeded):
    built = seeded()
    pane, view, lights = issued(built)
    published.archive(built.store, view, "race", lights)
    found = debrief.debrief_view(
        built.store, pane, no_sprints(), event_at(built.store, 2024, 6), "run-x"
    )
    assert found.called is not None
    assert found.called.run_id == view.run_id
    assert found.finish
    assert [item.position for item in found.finish if item.position is not None] == sorted(
        item.position for item in found.finish if item.position is not None
    )
    assert [(item.season, item.round) for item in found.record] == [(2024, 6)]


def test_before_the_race_the_call_stands_with_no_finish(seeded):
    built = seeded()
    pane, view, lights = issued(built)
    published.archive(built.store, view, "race", lights)
    unrun = dataclasses.replace(pane, results=pane.results.filter(pl.col("round") != 6))
    found = debrief.debrief_view(
        built.store, unrun, no_sprints(), event_at(built.store, 2024, 6), "run-x"
    )
    assert found.called is not None
    assert found.finish == []
    assert found.record == []


def test_nothing_published_means_no_call(seeded):
    built = seeded()
    pane, _, _ = issued(built)
    found = debrief.debrief_view(
        built.store, pane, no_sprints(), event_at(built.store, 2024, 6), "run-x"
    )
    assert found.called is None
