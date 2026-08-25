import numpy as np
import pytest
from pydantic import ValidationError

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
