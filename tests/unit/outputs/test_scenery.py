import json

import numpy as np
import pytest

from pitadvisor.ingest.terrain import Terrain
from pitadvisor.outputs import scenery
from pitadvisor.outputs.track_shape import Alignment, Track
from pitadvisor.outputs.view_contracts import SceneryView, emit_scenery, scenery_key

ALIGNMENT = Alignment(
    rotation=np.eye(2), offset=np.zeros(2), lat0=19.4, lon0=-99.09, median_m=3.0, p95_m=8.0
)


def square_track():
    side = np.linspace(0, 400, 81)
    xy = np.vstack(
        [
            np.c_[side - 200, np.full(81, -100.0)],
            np.c_[np.full(41, 200.0), np.linspace(-100, 100, 41)],
            np.c_[200 - side, np.full(81, 100.0)],
            np.c_[np.full(41, -200.0), np.linspace(100, -100, 41)],
        ]
    )
    return Track(
        points=np.c_[xy, np.linspace(0, 2, len(xy))], length_m=1200.0, sectors_m=[400.0, 800.0]
    )


def nodes(xy):
    lat, lon = ALIGNMENT.to_latlon(np.asarray(xy, dtype=float))
    return [{"lat": float(a), "lon": float(o)} for a, o in zip(lat, lon, strict=True)]


def box(x, y, half=10.0):
    return nodes(
        [
            [x - half, y - half],
            [x + half, y - half],
            [x + half, y + half],
            [x - half, y + half],
            [x - half, y - half],
        ]
    )


def flat_terrain(level=2240.0):
    grid = np.full((121, 121), level)
    return Terrain({(19, -100): grid})


ELEMENTS = [
    {"type": "way", "id": 1, "tags": {"building": "yes", "height": "21 m"}, "geometry": box(0, 0)},
    {
        "type": "way",
        "id": 2,
        "tags": {"building": "grandstand", "building:levels": "4"},
        "geometry": box(0, 140),
    },
    {"type": "way", "id": 3, "tags": {"building": "house"}, "geometry": box(300, 300)},
    {"type": "way", "id": 4, "tags": {"building": "yes"}, "geometry": box(2000, 0)},
    {"type": "way", "id": 5, "tags": {"leisure": "park"}, "geometry": box(-300, 300, half=60)},
    {"type": "way", "id": 6, "tags": {"natural": "water"}, "geometry": box(0, -300, half=40)},
    {
        "type": "way",
        "id": 7,
        "tags": {"highway": "primary"},
        "geometry": nodes([[-600, -200], [600, -200]]),
    },
    {
        "type": "way",
        "id": 8,
        "tags": {"highway": "footway"},
        "geometry": nodes([[-50, 50], [50, 50]]),
    },
    {
        "type": "way",
        "id": 9,
        "tags": {"railway": "rail"},
        "geometry": nodes([[-600, 250], [600, 250]]),
    },
    {"type": "node", "id": 10, "tags": {"natural": "tree"}, **nodes([[0, -100]])[0]},
    {"type": "node", "id": 11, "tags": {"natural": "tree"}, **nodes([[0, 0]])[0]},
    {"type": "node", "id": 12, "tags": {"natural": "tree"}, **nodes([[-100, 0]])[0]},
]


def test_a_tagged_height_wins_then_levels_then_a_guess():
    assert scenery.building_height(1, {"building": "yes", "height": "21 m"}) == (21.0, False)
    assert scenery.building_height(1, {"building": "yes", "building:levels": "5"}) == (16.0, False)
    guess, estimated = scenery.building_height(42, {"building": "apartments"})
    assert estimated
    assert 12.0 <= guess <= 30.0
    assert scenery.building_height(42, {"building": "apartments"}) == (guess, True)
    assert scenery.building_height(43, {"building": "apartments"}) != (guess, True)
    low, high = scenery.DEFAULT_HEIGHT
    assert low <= scenery.building_height(7, {"building": "mystery"})[0] <= high


def test_area_kinds_follow_the_painting_table():
    assert scenery.area_kind({"landuse": "residential"}) == "urban"
    assert scenery.area_kind({"leisure": "garden"}) == "grass"
    assert scenery.area_kind({"landuse": "forest"}) == "wood"
    assert scenery.area_kind({"amenity": "parking"}) == "parking"
    assert scenery.area_kind({"shop": "bakery"}) is None


def test_trees_stay_off_the_track_out_of_buildings_and_inside_the_plinth():
    track = square_track().points
    footprint = np.array([[-10.0, -10.0], [10.0, -10.0], [10.0, 10.0], [-10.0, 10.0]])
    candidates = np.array([[0.0, -100.0], [0.0, 0.0], [-100.0, 0.0], [5000.0, 0.0]])
    kept = scenery.place_trees(
        candidates, scenery.plinth_box(track), track, [footprint], np.random.default_rng(1)
    )
    assert kept.tolist() == [[-100.0, 0.0]]


def test_the_tree_cap_samples_evenly_and_reproducibly():
    candidates = np.random.default_rng(0).uniform(-150, 150, (500, 2))
    candidates[:, 1] *= 0.4
    track = square_track().points
    first = scenery.place_trees(
        candidates, scenery.plinth_box(track), track, [], np.random.default_rng(7), cap=50
    )
    again = scenery.place_trees(
        candidates, scenery.plinth_box(track), track, [], np.random.default_rng(7), cap=50
    )
    assert len(first) == 50
    assert np.array_equal(first, again)


def test_the_view_carries_the_scene_in_the_lap_frame(store):
    view = scenery.scenery_view(
        "rodriguez",
        square_track(),
        ALIGNMENT,
        ELEMENTS,
        flat_terrain(),
        ["osm", "terrain"],
        "run-1",
    )
    assert [building.kind for building in view.buildings] == ["building", "grandstand", "building"]
    assert [(b.h, b.estimated) for b in view.buildings[:2]] == [(21.0, False), (12.8, False)]
    assert view.buildings[2].estimated
    assert [area.kind for area in view.areas] == ["grass", "water"]
    assert [(road.kind, road.lit) for road in view.roads] == [
        ("footway", False),
        ("rail", False),
        ("primary", True),
    ]
    trees = np.array(view.trees).reshape(-1, 3)
    assert [-100.0, 0.0] in trees[:, :2].tolist()
    assert all(abs(x) > 15 or abs(y) > 15 for x, y in trees[:, :2])
    minx, _, maxx, _ = view.plinth.box
    assert (minx, maxx) == (-750.0, 750.0)
    gx, gy = view.plinth.grid
    assert len(view.plinth.heights) == gx * gy
    # a flat tile means the whole plinth sits level with the lap, less the gap under the slab
    assert max(view.plinth.heights) <= 2.0
    assert view.track.sectors_m == [400.0, 800.0]

    emit_scenery(store, view)
    body = store.get(scenery_key("rodriguez"))
    assert b"\n" not in body
    assert SceneryView.model_validate_json(body).circuit_id == "rodriguez"
    assert json.loads(body)["view"] == "scenery_view"


def test_raceway_lines_skip_karting_and_single_nodes():
    elements = [
        {"type": "way", "tags": {"highway": "raceway"}, "geometry": nodes([[0, 0], [10, 0]])},
        {
            "type": "way",
            "tags": {"highway": "raceway", "sport": "karting"},
            "geometry": nodes([[0, 0], [10, 0]]),
        },
        {"type": "way", "tags": {"highway": "raceway"}, "geometry": nodes([[0, 0]])},
    ]
    assert len(scenery.raceway_lines(elements)) == 1


@pytest.mark.parametrize("element", [{"type": "node"}, {"type": "relation", "members": []}])
def test_shapes_without_geometry_give_no_rings(element):
    assert scenery.rings(element, scenery.track_frame(ALIGNMENT)) == []
