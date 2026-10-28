from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from pitadvisor.outputs import track_shape

START = datetime(2025, 10, 25, 21, 0, tzinfo=UTC)


def ellipse(samples=400, a=600.0, b=300.0, lap_seconds=80.0):
    """A lap around an ellipse at constant angular speed, in openf1's decimetres."""
    theta = np.linspace(0.0, 2 * np.pi, samples, endpoint=False)
    xy = np.c_[a * np.cos(theta), b * np.sin(theta)]
    z = 5.0 + 5.0 * np.sin(theta)
    times = theta / (2 * np.pi) * lap_seconds
    rows = [
        {
            "date": (START + timedelta(seconds=float(t))).isoformat(),
            "x": round(x * 10),
            "y": round(y * 10),
            "z": round(h * 10),
        }
        for (x, y), h, t in zip(xy, z, times, strict=True)
    ]
    return rows, {"date_start": START.isoformat()}


def perimeter(a, b):
    h = ((a - b) / (a + b)) ** 2
    return np.pi * (a + b) * (1 + 3 * h / (10 + np.sqrt(4 - 3 * h)))


def test_a_lap_comes_back_at_its_length_in_five_metre_steps():
    rows, lap = ellipse()
    points, times = track_shape.lap_samples(rows, lap)
    track = track_shape.resample(points, times, [20.0, 40.0])
    assert track.length_m == pytest.approx(perimeter(600, 300), rel=0.003)
    steps = np.linalg.norm(np.diff(track.points[:, :2], axis=0), axis=1)
    assert steps.mean() == pytest.approx(5.0, rel=0.01)
    assert np.allclose(track.points[:, :2].mean(axis=0), 0.0, atol=1e-6)
    assert track.points[:, 2].min() == pytest.approx(0.0)


def test_sector_boundaries_land_where_the_lap_was_at_that_time():
    rows, lap = ellipse()
    points, times = track_shape.lap_samples(rows, lap)
    track = track_shape.resample(points, times, [20.0, 40.0])
    quarter, half = track.length_m / 4, track.length_m / 2
    assert track.sectors_m[0] == pytest.approx(quarter, abs=30)
    assert track.sectors_m[1] == pytest.approx(half, abs=30)


def test_samples_where_the_car_did_not_move_are_dropped():
    rows, lap = ellipse(samples=50)
    stopped = [rows[0], {**rows[0], "date": rows[1]["date"]}, *rows[1:]]
    points, times = track_shape.lap_samples(stopped, lap)
    assert len(points) == 50
    assert len(times) == 50


@pytest.mark.parametrize(("degrees", "flip"), [(0, 1), (37, 1), (200, 1), (73, -1)])
def test_the_lap_is_found_on_the_raceway_however_it_was_turned(degrees, flip):
    rows, lap = ellipse(a=700, b=250)
    points, times = track_shape.lap_samples(rows, lap)
    track = track_shape.resample(points, times, [20.0, 40.0]).points
    # make the shape asymmetric, an ellipse fits itself mirrored and rotated by half a turn
    track[:, 0] += np.where(track[:, 1] > 0, 0.0, 0.002 * track[:, 0] ** 2 / 10)
    theta = np.radians(degrees)
    rotation = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
    mapped = (track[:, :2] * np.array([1.0, flip])) @ rotation.T + np.array([120.0, -80.0])
    lat0, lon0 = 19.4, -99.09
    lat = lat0 + mapped[:, 1] / track_shape.METRES_PER_DEG_LAT
    lon = lon0 + mapped[:, 0] / (np.cos(np.radians(lat0)) * track_shape.METRES_PER_DEG_LON)
    raceway = [np.c_[lat[:400], lon[:400]], np.c_[lat[399:], lon[399:]]]
    fitted = track_shape.align(track[:, :2], raceway)
    assert fitted.median_m < 0.5
    back_lat, back_lon = fitted.to_latlon(track[:, :2])
    assert np.allclose(back_lat, lat, atol=1e-5)
    assert np.allclose(back_lon, lon, atol=1e-5)
    assert np.sign(np.linalg.det(fitted.rotation)) == flip


def test_ground_under_the_track_sits_below_the_slab_and_eases_back():
    track = np.c_[np.linspace(0, 500, 101), np.zeros(101), np.full(101, 4.0)]
    cells = np.array([[250.0, 0.0], [250.0, 40.0], [250.0, 150.0], [250.0, 400.0]])
    hills = np.full(4, 60.0)
    flattened = track_shape.flatten_under(cells, hills, track)
    assert flattened[0] == pytest.approx(4.0 - track_shape.UNDER_TRACK_M)
    assert flattened[1] == pytest.approx(4.0 - track_shape.UNDER_TRACK_M)
    assert 1.0 < flattened[2] < 60.0
    assert flattened[3] == pytest.approx(60.0)
    assert (flattened[:2] <= track[0, 2] - 2.7).all()


def test_points_in_a_polygon_follow_the_even_odd_rule():
    ring = np.array([[0.0, 0.0], [10.0, 0.0], [10.0, 10.0], [5.0, 4.0], [0.0, 10.0]])
    points = np.array([[2.0, 2.0], [5.0, 8.0], [-1.0, 5.0], [9.5, 9.0], [11.0, 1.0]])
    assert track_shape.inside(points, ring).tolist() == [True, False, False, True, False]
