import json

import pytest

from pitadvisor.outputs.brief import StaleViewsError, brief_view, from_store
from pitadvisor.outputs.view_contracts import emit, forecast_view, track_view, weekend_view
from tests.unit.outputs.test_view_contracts import NOW, assembled_for, predicted_for


def views(built, round_=6):
    assembled = assembled_for(built, round_)
    _, context, forecast, seats, grid = predicted_for(built, round_)
    return (
        weekend_view(assembled),
        forecast_view(forecast, context, "run-1", seats, grid, generated_at=NOW),
        track_view(assembled),
    )


def test_the_brief_copies_its_favourites_from_the_forecast(seeded):
    weekend, forecast, track = views(seeded())
    brief = brief_view(weekend, forecast, track, "run-2", NOW)
    assert brief.view == "brief_view"
    assert 0 < len(brief.favourites) <= 3
    wins = [row.win for row in brief.favourites]
    assert wins == sorted(wins, reverse=True)
    by_code = {row.driver_code: row for row in forecast.drivers}
    for row in brief.favourites:
        source = by_code[row.driver_code]
        # copied, never recomputed: the brief may not hold a number the view does not
        assert (row.win, row.podium, row.position_low, row.position_high) == (
            source.win,
            source.podium,
            source.position_low,
            source.position_high,
        )


def test_form_leaders_are_still_racing_and_carry_their_interval(seeded):
    weekend, forecast, track = views(seeded())
    brief = brief_view(weekend, forecast, track, "run-2", NOW)
    current = max(row.last_season for row in weekend.drivers)
    racing = {row.driver_code for row in weekend.drivers if row.last_season == current}
    assert brief.form_leaders
    values = [leader.form.value for leader in brief.form_leaders]
    # negative is faster, so the fastest come first
    assert values == sorted(values)
    fastest = min(
        row.form.value for row in weekend.drivers if row.form and row.driver_code in racing
    )
    assert values[0] == fastest
    for leader in brief.form_leaders:
        assert leader.driver_code in racing
        assert leader.form.low <= leader.form.value <= leader.form.high


def test_the_brief_names_the_track_and_where_it_came_from(seeded):
    weekend, forecast, track = views(seeded())
    brief = brief_view(weekend, forecast, track, "run-2", NOW)
    assert brief.track.circuit_id == track.profile.circuit_id
    assert len(brief.track.neighbours) <= 3
    assert track.profile.circuit_id not in brief.track.neighbours
    assert [source.view for source in brief.sources] == [
        "weekend_view",
        "forecast_view",
        "track_view",
    ]
    assert brief.event == weekend.event


def test_views_from_two_different_races_are_refused(seeded):
    built = seeded()
    weekend, _, track = views(built, 6)
    _, other, _ = views(built, 5)
    with pytest.raises(StaleViewsError, match="different races"):
        brief_view(weekend, other, track, "run-2", NOW)


def test_the_brief_is_read_back_off_the_emitted_views(seeded, store):
    for view in views(seeded()):
        emit(store, view)
    emit(store, from_store(store, "run-3"))
    payload = json.loads(store.get("views/brief_view.json"))
    assert payload["run_id"] == "run-3"
    assert payload["simulated_paths"] == 200


def test_before_qualifying_the_brief_says_the_grid_was_sampled(seeded):
    import dataclasses

    import numpy as np
    import polars as pl

    from pitadvisor.model import backtest

    built = seeded()
    assembled = assembled_for(built)
    pane = backtest.panel(built.store)
    context = assembled.metrics.context
    held = (pl.col("season") == context.season) & (pl.col("round") == context.round)
    early = dataclasses.replace(
        pane,
        results=pane.results.filter(~held),
        quali=pane.quali.filter(~held),
        qualified={
            key: order
            for key, order in pane.qualified.items()
            if key != (context.season, context.round)
        },
    )
    predicted = backtest.forecast(
        early, context, context.race_date, np.random.default_rng(5), paths=200
    )
    seats, _ = backtest.seats_for(early, context, context.race_date)
    grid = backtest.grid_for(early, context, predicted.outcome.driver_code)
    forecast = forecast_view(predicted, context, "run-1", seats, grid, generated_at=NOW)
    assert forecast.grid_sampled
    assert all(row.grid is None for row in forecast.drivers)
    brief = brief_view(weekend_view(assembled), forecast, track_view(assembled), "run-2", NOW)
    assert brief.grid_sampled
    assert all(row.grid is None for row in brief.favourites)
