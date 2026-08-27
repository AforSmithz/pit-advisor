from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from pydantic import ValidationError

from pitadvisor.features import track_fit
from pitadvisor.features.track_fit import (
    DEMANDS,
    FEATURES,
    CircuitProfile,
    load,
    matrix,
    vector,
)


def test_the_shipped_taxonomy_parses():
    profiles = load()
    assert len(profiles) >= 24
    assert profiles["monaco"].length_km == 3.337
    assert profiles["rodriguez"].altitude_m == 2240


def test_every_circuit_carries_the_whole_demand_vector():
    for profile in load().values():
        assert all(1 <= getattr(profile.demand, name) <= 5 for name in DEMANDS)


def test_a_reprofiled_circuit_names_the_season_the_layout_changed():
    profiles = load()
    assert profiles["albert_park"].reprofiled == 2022
    assert profiles["monza"].reprofiled is None


def test_a_demand_band_outside_one_to_five_is_rejected():
    with pytest.raises(ValidationError):
        CircuitProfile(
            circuit_id="nowhere",
            length_km=5.0,
            corners=12,
            direction="clockwise",
            altitude_m=10,
            demand={"downforce": 6, **{name: 3 for name in DEMANDS if name != "downforce"}},
        )


def test_the_vector_follows_the_declared_feature_order():
    assert len(vector(load()["monza"])) == len(FEATURES)


def test_the_matrix_is_scaled_so_altitude_does_not_swamp_a_demand_band():
    _, scaled = matrix(load())
    assert scaled.shape[1] == len(FEATURES)
    assert np.allclose(scaled.mean(axis=0), 0, atol=1e-9)
    assert np.allclose(scaled.std(axis=0), 1, atol=1e-9)


HALF_LIFE = 20.0


def history(
    sensitivity: dict[str, float],
    profiles: dict[str, track_fit.CircuitProfile],
    seasons: tuple[int, ...] = (2022, 2023, 2024),
    noise: float = 0.10,
    seed: int = 7,
) -> pl.DataFrame:
    """Team pace is a known linear function of one demand band, so a recovered prediction
    can be checked against the value that generated it."""
    rng = np.random.default_rng(seed)
    ids, scaled = track_fit.matrix(profiles)
    downforce = {name: scaled[position][3] for position, name in enumerate(ids)}
    rows = []
    for season in seasons:
        for round_, circuit_id in enumerate(ids, start=1):
            for team, slope in sensitivity.items():
                rows.append(
                    {
                        "season": season,
                        "round": round_,
                        "race_date": date(season, 1, 1) + timedelta(days=12 * round_),
                        "circuit_id": circuit_id,
                        "constructor_id": team,
                        "value": slope * downforce[circuit_id] + rng.normal(0.0, noise),
                    }
                )
    return pl.DataFrame(rows)


def truth_at(circuit_id: str, slope: float, profiles: dict[str, track_fit.CircuitProfile]) -> float:
    ids, scaled = track_fit.matrix(profiles)
    return slope * scaled[ids.index(circuit_id)][3]


def test_the_regression_recovers_a_known_circuit_sensitivity():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5, "bravo": -0.8}, profiles)
    fitted = track_fit.fit(frame, "monaco", date(2025, 1, 1), profiles, half_life=HALF_LIFE)
    found = {item.constructor_id: item for item in fitted.regression}
    for team, slope in (("alpha", 1.5), ("bravo", -0.8)):
        expected = truth_at("monaco", slope, profiles)
        assert found[team].interval_low <= expected <= found[team].interval_high


def test_the_two_estimators_agree_when_the_signal_is_clean():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5, "bravo": -0.8}, profiles)
    fitted = track_fit.fit(frame, "monza", date(2025, 1, 1), profiles, half_life=HALF_LIFE)
    assert fitted.disagreements == []


def test_a_circuit_is_its_own_nearest_neighbour():
    close = track_fit.nearest("monaco", track_fit.load())
    assert close[0].circuit_id == "monaco"
    assert close[0].similarity == pytest.approx(1.0)


def test_the_neighbours_of_monza_are_the_other_low_downforce_tracks():
    profiles = track_fit.load()
    close = [n.circuit_id for n in track_fit.nearest("monza", profiles, k=4)]
    assert "monza" in close
    assert profiles["monza"].demand.downforce == 1
    assert all(profiles[name].demand.downforce <= 3 for name in close)


def test_similarity_never_weights_an_opposite_circuit_negatively():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5}, profiles)
    fitted = track_fit.fit(frame, "monza", date(2025, 1, 1), profiles, half_life=HALF_LIFE)
    assert all(item.similarity > 0.0 for item in fitted.neighbours[:1])
    assert fitted.similarity[0].effective_samples > 0.0


def test_the_fit_refuses_a_circuit_it_has_no_profile_for():
    with pytest.raises(track_fit.UnknownCircuitError):
        track_fit.fit(pl.DataFrame(), "nurburgring", date(2025, 1, 1), track_fit.load())


def test_nothing_after_the_as_of_date_reaches_either_estimator():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5}, profiles, seasons=(2022, 2023, 2024))
    cut = track_fit.fit(frame, "monza", date(2023, 1, 1), profiles, half_life=HALF_LIFE)
    assert cut.events_dropped == frame.filter(pl.col("season") > 2022).height
    assert cut.events_used <= len(profiles)


def test_a_reprofiled_circuit_drops_its_races_from_the_old_layout():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5}, profiles, seasons=(2021, 2022, 2023, 2024))
    fitted = track_fit.fit(frame, "monza", date(2025, 1, 1), profiles, half_life=HALF_LIFE)
    reprofiled = [c for c, p in profiles.items() if p.reprofiled is not None]
    assert reprofiled
    assert fitted.dropped_reprofiled == sum(
        1 for c in reprofiled for s in (2021, 2022, 2023, 2024) if s < (profiles[c].reprofiled or 0)
    )


def test_a_divergence_is_reported_rather_than_averaged_away():
    profiles = track_fit.load()
    frame = history({"alpha": 3.0}, profiles)
    # a penalty this heavy flattens the regression onto the team's grand mean while the
    # lookalikes still see monaco for what it is. the two then genuinely disagree
    fitted = track_fit.fit(
        frame, "monaco", date(2025, 1, 1), profiles, half_life=HALF_LIFE, ridge=1e6
    )
    assert fitted.disagreements == ["alpha"]
    assert fitted.regression[0].estimate == pytest.approx(0.0, abs=0.5)
    assert fitted.similarity[0].estimate > 2.0


def test_both_estimators_carry_a_race_to_race_spread_beside_their_error():
    profiles = track_fit.load()
    frame = history({"alpha": 1.5}, profiles, noise=0.4)
    fitted = track_fit.fit(frame, "monza", date(2025, 1, 1), profiles, half_life=HALF_LIFE)
    for item in (fitted.regression[0], fitted.similarity[0]):
        assert item.spread > item.standard_error
