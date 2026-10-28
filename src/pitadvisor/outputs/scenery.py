# scipy's splines and kd-trees ship no stubs, so everything they return reads as unknown
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from itertools import pairwise
from typing import Any, Final

import numpy as np
from scipy.interpolate import RegularGridInterpolator
from scipy.spatial import KDTree

from pitadvisor.ingest.terrain import Terrain
from pitadvisor.outputs.track_shape import (
    STEP_M,
    Alignment,
    Array,
    Track,
    flatten_under,
    inside,
    to_local,
)
from pitadvisor.outputs.view_contracts import (
    SceneryAlignment,
    SceneryArea,
    SceneryBuilding,
    SceneryPlinth,
    SceneryRoad,
    SceneryTrack,
    SceneryView,
)

MARGIN_M: Final = 550.0
CELL_M: Final = 16.0
INSET_M: Final = 8.0
LEVEL_M: Final = 3.2
TREE_AREA_M2: Final = 220.0
TREE_ROW_STEP_M: Final = 8.0
TREE_CLEAR_M: Final = 18.0
TREE_CAP: Final = 7000
SEED: Final = 7

# painted in this order, so the later kinds sit on top
AREAS: Final[list[tuple[str, frozenset[str], str]]] = [
    ("landuse", frozenset({"residential", "commercial", "retail", "industrial"}), "urban"),
    ("landuse", frozenset({"grass", "greenfield", "meadow", "village_green"}), "grass"),
    ("leisure", frozenset({"park", "garden"}), "grass"),
    ("leisure", frozenset({"pitch", "track", "sports_centre", "stadium"}), "pitch"),
    ("natural", frozenset({"wood", "scrub", "grassland"}), "wood"),
    ("landuse", frozenset({"forest"}), "wood"),
    ("amenity", frozenset({"parking"}), "parking"),
    ("natural", frozenset({"water"}), "water"),
    ("leisure", frozenset({"swimming_pool"}), "pool"),
]
ROAD_WIDTHS: Final = {
    "motorway": 18.0,
    "trunk": 16.0,
    "primary": 14.0,
    "secondary": 11.0,
    "tertiary": 9.0,
    "motorway_link": 8.0,
    "primary_link": 8.0,
    "secondary_link": 7.0,
    "residential": 6.0,
    "unclassified": 6.0,
    "busway": 6.0,
    "service": 4.0,
    "pedestrian": 4.0,
    "footway": 1.6,
    "path": 1.4,
    "steps": 1.6,
}
LIT: Final = frozenset({"motorway", "trunk", "primary", "secondary"})
RAIL_WIDTH_M: Final = 3.0
# only where openstreetmap has neither a height nor a level count, which is most buildings
GUESSED_HEIGHT: Final = {
    "house": (5.0, 8.0),
    "residential": (9.0, 18.0),
    "apartments": (12.0, 30.0),
    "retail": (4.0, 7.0),
    "commercial": (8.0, 16.0),
    "warehouse": (7.0, 10.0),
    "school": (7.0, 11.0),
    "university": (10.0, 16.0),
    "grandstand": (12.0, 18.0),
    "roof": (4.0, 6.0),
    "dormitory": (10.0, 16.0),
}
DEFAULT_HEIGHT: Final = (5.0, 10.0)
BUILDING_KINDS: Final = {"grandstand": "grandstand", "roof": "canopy"}
TREE_AREAS: Final = (("natural", "wood"), ("landuse", "forest"), ("leisure", "park"))

Box = tuple[float, float, float, float]
ToTrack = Callable[[list[dict[str, Any]]], Array]


def track_frame(alignment: Alignment) -> ToTrack:
    def project(geometry: list[dict[str, Any]]) -> Array:
        lat = np.array([float(node["lat"]) for node in geometry])
        lon = np.array([float(node["lon"]) for node in geometry])
        local = to_local(lat, lon, alignment.lat0, alignment.lon0)
        return (local - alignment.offset) @ alignment.rotation

    return project


def raceway_lines(elements: list[dict[str, Any]]) -> list[Array]:
    return [
        np.array([[float(node["lat"]), float(node["lon"])] for node in element["geometry"]])
        for element in elements
        if element.get("type") == "way"
        and element.get("tags", {}).get("sport", "motor") == "motor"
        and len(element.get("geometry", [])) > 1
    ]


def rings(element: dict[str, Any], project: ToTrack) -> list[Array]:
    if element.get("type") == "way" and element.get("geometry"):
        return [project(element["geometry"])]
    if element.get("type") == "relation":
        return [
            project(member["geometry"])
            for member in element.get("members", [])
            if member.get("role") == "outer" and member.get("geometry")
        ]
    return []


def plinth_box(track: Array, margin: float = MARGIN_M) -> Box:
    low, high = track[:, :2].min(axis=0) - margin, track[:, :2].max(axis=0) + margin
    return float(low[0]), float(low[1]), float(high[0]), float(high[1])


def _touches(ring: Array, box: Box) -> bool:
    return bool(
        ring[:, 0].max() >= box[0]
        and ring[:, 0].min() <= box[2]
        and ring[:, 1].max() >= box[1]
        and ring[:, 1].min() <= box[3]
    )


def _flat(points: Array) -> list[float]:
    return [round(float(value), 1) for value in points.ravel()]


def area_kind(tags: dict[str, str]) -> str | None:
    found = None
    for key, values, kind in AREAS:
        if tags.get(key) in values:
            found = kind
    return found


def areas(elements: list[dict[str, Any]], project: ToTrack, box: Box) -> list[SceneryArea]:
    grouped: dict[str, list[list[float]]] = {}
    for element in elements:
        kind = area_kind(element.get("tags", {}))
        if kind is None:
            continue
        for ring in rings(element, project):
            if len(ring) > 2 and _touches(ring, box):
                grouped.setdefault(kind, []).append(_flat(ring))
    order = list(dict.fromkeys(kind for _, _, kind in AREAS))
    return [SceneryArea(kind=kind, rings=grouped[kind]) for kind in order if kind in grouped]


def roads(elements: list[dict[str, Any]], project: ToTrack, box: Box) -> list[SceneryRoad]:
    found: list[SceneryRoad] = []
    for element in elements:
        if element.get("type") != "way" or not element.get("geometry"):
            continue
        tags = element.get("tags", {})
        highway, railway = tags.get("highway"), tags.get("railway")
        if highway in ROAD_WIDTHS:
            kind, width, lit = str(highway), ROAD_WIDTHS[str(highway)], highway in LIT
        elif railway:
            kind, width, lit = "rail", RAIL_WIDTH_M, False
        else:
            continue
        line = project(element["geometry"])
        if len(line) > 1 and _touches(line, box):
            found.append(SceneryRoad(kind=kind, width_m=width, lit=lit, points=_flat(line)))
    return sorted(found, key=lambda road: road.width_m)


def _number(text: str | None) -> float | None:
    if text is None:
        return None
    try:
        return float(text.replace("m", "").strip())
    except ValueError:
        return None


def building_height(osm_id: int, tags: dict[str, str]) -> tuple[float, bool]:
    measured = _number(tags.get("height"))
    if measured is not None and measured > 0:
        return measured, False
    levels = _number(tags.get("building:levels"))
    if levels is not None and levels > 0:
        return levels * LEVEL_M, False
    low, high = GUESSED_HEIGHT.get(tags.get("building", ""), DEFAULT_HEIGHT)
    # the same building gets the same guess on every bake
    share = int(hashlib.md5(str(osm_id).encode()).hexdigest()[:6], 16) / 0xFFFFFF
    return round(low + (high - low) * share, 1), True


def _inset(box: Box, by: float) -> Box:
    return box[0] + by, box[1] + by, box[2] - by, box[3] - by


def _within(points: Array, box: Box) -> np.ndarray[Any, np.dtype[np.bool_]]:
    return (
        (points[:, 0] >= box[0])
        & (points[:, 0] <= box[2])
        & (points[:, 1] >= box[1])
        & (points[:, 1] <= box[3])
    )


def buildings(
    elements: list[dict[str, Any]],
    project: ToTrack,
    box: Box,
    ground: Callable[[Array], Array],
) -> tuple[list[SceneryBuilding], list[Array]]:
    found: list[SceneryBuilding] = []
    footprints: list[Array] = []
    limit = _inset(box, INSET_M)
    for element in elements:
        tags = element.get("tags", {})
        if not tags.get("building"):
            continue
        for ring in rings(element, project):
            if len(ring) < 4 or not _within(ring, limit).all():
                continue
            if np.allclose(ring[0], ring[-1]):
                ring = ring[:-1]
            height, estimated = building_height(int(element.get("id", 0)), tags)
            base = float(ground(ring.mean(axis=0, keepdims=True))[0])
            found.append(
                SceneryBuilding(
                    h=round(height, 1),
                    base=round(base, 1),
                    kind=BUILDING_KINDS.get(str(tags["building"]), "building"),
                    estimated=estimated,
                    points=_flat(ring),
                )
            )
            footprints.append(ring)
    return found, footprints


def tree_candidates(
    elements: list[dict[str, Any]], project: ToTrack, rng: np.random.Generator
) -> Array:
    picked: list[Array] = []
    for element in elements:
        tags = element.get("tags", {})
        if element.get("type") == "node" and tags.get("natural") == "tree":
            picked.append(project([element]))
        elif element.get("type") == "way" and tags.get("natural") == "tree_row":
            line = project(element.get("geometry", []))
            for a, b in pairwise(line):
                steps = np.arange(
                    0.0, 1.0, TREE_ROW_STEP_M / max(float(np.linalg.norm(b - a)), 1.0)
                )
                picked.append(a + (b - a) * steps[:, None])
        elif any(tags.get(key) == value for key, value in TREE_AREAS):
            for ring in rings(element, project):
                if len(ring) < 4:
                    continue
                low, high = ring.min(axis=0), ring.max(axis=0)
                count = int(np.prod(high - low) / TREE_AREA_M2)
                scatter = low + rng.random((count, 2)) * (high - low)
                picked.append(scatter[inside(scatter, ring)])
    return np.vstack(picked) if picked else np.empty((0, 2))


def place_trees(
    candidates: Array,
    box: Box,
    track: Array,
    footprints: list[Array],
    rng: np.random.Generator,
    cap: int = TREE_CAP,
) -> Array:
    kept = candidates[_within(candidates, box)]
    if len(kept) and len(track):
        kept = kept[KDTree(track[:, :2]).query(kept)[0] > TREE_CLEAR_M]
    if footprints and len(kept):
        blocked = np.zeros(len(kept), dtype=bool)
        tree = KDTree(kept)
        for ring in footprints:
            centre = ring.mean(axis=0)
            reach = float(np.linalg.norm(ring - centre, axis=1).max())
            near = np.array(tree.query_ball_point(centre, reach), dtype=int)
            if len(near):
                blocked[near[inside(kept[near], ring)]] = True
        kept = kept[~blocked]
    if len(kept) > cap:
        kept = kept[np.sort(rng.choice(len(kept), cap, replace=False))]
    return kept


def plinth(
    box: Box, track: Array, alignment: Alignment, terrain: Terrain, cell: float = CELL_M
) -> tuple[int, int, Array]:
    gx = round((box[2] - box[0]) / cell) + 1
    gy = round((box[3] - box[1]) / cell) + 1
    xs, ys = np.meshgrid(np.linspace(box[0], box[2], gx), np.linspace(box[1], box[3], gy))
    cells = np.c_[xs.ravel(), ys.ravel()]
    lat, lon = alignment.to_latlon(cells)
    raw = terrain.sample(lat, lon)
    tlat, tlon = alignment.to_latlon(track[:, :2])
    offset = float(np.nanmedian(terrain.sample(tlat, tlon) - track[:, 2]))
    heights = np.nan_to_num(raw - offset, nan=float(np.nanmedian(raw - offset)))
    return gx, gy, flatten_under(cells, heights, track).reshape(gy, gx)


def ground_at(box: Box, heights: Array) -> Callable[[Array], Array]:
    gy, gx = heights.shape
    lookup = RegularGridInterpolator(
        (np.linspace(box[1], box[3], gy), np.linspace(box[0], box[2], gx)),
        heights,
    )
    return lambda xy: lookup(np.c_[xy[:, 1], xy[:, 0]])


def scenery_view(
    circuit_id: str,
    track: Track,
    alignment: Alignment,
    elements: list[dict[str, Any]],
    terrain: Terrain,
    sources: list[str],
    run_id: str,
) -> SceneryView:
    rng = np.random.default_rng(SEED)
    points = track.points
    project = track_frame(alignment)
    box = plinth_box(points)
    gx, gy, heights = plinth(box, points, alignment, terrain)
    ground = ground_at(box, heights)
    found, footprints = buildings(elements, project, box, ground)
    trees = place_trees(tree_candidates(elements, project, rng), box, points, footprints, rng)
    tree_rows = np.c_[trees, ground(trees)] if len(trees) else np.empty((0, 3))
    return SceneryView(
        generated_at=datetime.now(UTC),
        run_id=run_id,
        circuit_id=circuit_id,
        track=SceneryTrack(
            length_m=round(track.length_m, 1),
            step_m=STEP_M,
            sectors_m=track.sectors_m,
            points=[[round(float(value), 2) for value in row] for row in points],
        ),
        alignment=SceneryAlignment(
            median_m=round(alignment.median_m, 2), p95_m=round(alignment.p95_m, 2)
        ),
        plinth=SceneryPlinth(
            box=[round(value, 1) for value in box], grid=[gx, gy], heights=_flat(heights)
        ),
        areas=areas(elements, project, box),
        roads=roads(elements, project, box),
        buildings=found,
        trees=_flat(tree_rows),
        sources=sources,
    )
