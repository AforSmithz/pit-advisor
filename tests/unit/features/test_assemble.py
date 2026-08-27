from datetime import UTC, date, datetime

import polars as pl
import pytest

from pitadvisor.features.assemble import (
    NoEventError,
    assemble,
    event_at,
    next_event,
    session_paces,
)

NOW = datetime(2025, 1, 1, tzinfo=UTC)


def assembled_for(built, round_: int = 6, season: int = 2024):
    context = event_at(built.store, season, round_)
    return assemble(built.store, context, "run-1", generated_at=NOW)


def test_the_next_event_is_the_first_one_still_ahead(seeded):
    found = next_event(seeded().store, today=date(2024, 4, 1))
    assert (found.season, found.round) == (2024, 4)
    assert found.circuit_id == "silverstone"


def test_out_of_season_it_falls_back_to_the_last_race_rather_than_failing(seeded):
    found = next_event(seeded().store, today=date(2026, 1, 1))
    assert (found.season, found.round) == (2024, 6)


def test_an_empty_lake_says_so(store):
    with pytest.raises(NoEventError):
        next_event(store, today=date(2024, 4, 1))


def test_an_event_outside_the_lake_is_named_in_the_error(seeded):
    with pytest.raises(NoEventError, match="2024 round 30"):
        event_at(seeded().store, 2024, 30)


def test_every_race_yields_a_dry_fit_and_a_wet_race_yields_a_wet_one(seeded):
    built = seeded(wet_rounds=(3,))
    paces, _ = session_paces(built.store)
    regimes = {(item.season, item.round): item.regime for item in paces}
    assert regimes[(2024, 1)] == "dry"
    assert regimes[(2024, 3)] == "wet"
    assert len(paces) == built.events


def test_a_lake_with_no_session_laps_reports_nothing_rather_than_failing(store):
    paces, skipped = session_paces(store)
    assert paces == []
    assert skipped == {}


def test_a_dry_race_records_why_its_wet_fit_was_skipped(seeded):
    built = seeded(wet_rounds=(3,))
    _, skipped = session_paces(built.store)
    assert skipped["wet: too few clean laps"] == built.events - len(built.seasons)
    assert "dry: too few clean laps" in skipped


def test_the_whole_stack_assembles_from_bronze(seeded):
    built = seeded(wet_rounds=(2, 3))
    metrics = assembled_for(built).metrics
    assert metrics.context.circuit_id == "suzuka"
    assert metrics.coverage.sessions_fitted == built.events
    assert sum(metrics.coverage.skips.values()) == metrics.coverage.sessions_skipped
    assert metrics.coverage.drivers_rated == len(built.codes)
    assert len(metrics.form.drivers) == len(built.codes)
    assert metrics.form.components == len(built.teams)
    assert len(metrics.track.regression) == len(built.teams)
    assert metrics.reliability.teams


def test_nothing_from_the_event_itself_reaches_its_own_metrics(seeded):
    built = seeded()
    assembled = assembled_for(built, round_=6)
    cutoff = built.held(2024, 6)
    assert assembled.metrics.as_of == cutoff
    # the frame still carries the event, every fit is what has to stop short of it
    assert assembled.pace.filter(pl.col("race_date") >= cutoff).height > 0
    assert all(when < cutoff for when, _ in assembled.quali)
    assert assembled.metrics.form.events_used == built.events - 1
    assert assembled.metrics.form.events_dropped > 0


def test_the_wet_races_are_the_only_ones_the_wet_delta_stands_on(seeded):
    built = seeded(wet_rounds=(2, 3))
    metrics = assembled_for(built).metrics
    assert metrics.wet.wet_sessions == 2 * len(built.seasons)
    assert metrics.coverage.wet_sessions == 2 * len(built.seasons)


def test_the_dry_forecast_for_the_event_is_carried_through(seeded):
    weather = assembled_for(seeded()).metrics.weather
    assert weather is not None
    assert weather.dry == 1.0
    assert weather.hours == 3


def test_the_exclusion_counts_are_pooled_across_every_session(seeded):
    built = seeded()
    coverage = assembled_for(built).metrics.coverage
    assert coverage.total_laps == built.events * len(built.codes) * built.laps
    assert coverage.clean_laps < coverage.total_laps
    assert sum(coverage.exclusions.values()) == coverage.total_laps - coverage.clean_laps
    assert "opening_laps" in coverage.exclusions
    assert "in_lap" in coverage.exclusions
