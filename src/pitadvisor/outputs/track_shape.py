# scipy's splines and kd-trees ship no stubs, so everything they return reads as unknown
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false
# pyright: reportUnknownArgumentType=false, reportUnknownParameterType=false
from datetime import datetime
from itertools import pairwise
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from pydantic import BaseModel, ConfigDict
from scipy.interpolate import splev, splprep
from scipy.spatial import KDTree

STEP_M: Final = 5.0
DENSE: Final = 20000
METRES_PER_DEG_LAT: Final = 110540.0
METRES_PER_DEG_LON: Final = 111320.0
FLAT_M: Final = 50.0
BLEND_M: Final = 200.0
# the slab is drawn 12 m deep, so ground a few metres under the racing line never shows through
UNDER_TRACK_M: Final = 3.0
# share of the published circuit length a baked lap may miss by before an older one is tried
LENGTH_TOLERANCE: Final = 0.005

Array = npt.NDArray[np.float64]


class Track(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    points: Array
    length_m: float
    sectors_m: list[float]


class Alignment(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    rotation: Array
    offset: Array
    lat0: float
    lon0: float
    median_m: float
    p95_m: float

    def to_latlon(self, xy: Array) -> tuple[Array, Array]:
        local = xy @ self.rotation.T + self.offset
        return (
            self.lat0 + local[:, 1] / METRES_PER_DEG_LAT,
            self.lon0 + local[:, 0] / (np.cos(np.radians(self.lat0)) * METRES_PER_DEG_LON),
        )


def _seconds(stamp: str, origin: datetime) -> float:
    return (datetime.fromisoformat(stamp) - origin).total_seconds()


def lap_samples(location: list[dict[str, Any]], lap: dict[str, Any]) -> tuple[Array, Array]:
    origin = datetime.fromisoformat(str(lap["date_start"]))
    # openf1 positions are decimetres
    points = np.array([[row["x"], row["y"], row["z"]] for row in location], dtype=np.float64) / 10.0
    times = np.array([_seconds(str(row["date"]), origin) for row in location], dtype=np.float64)
    moved = np.r_[True, np.linalg.norm(np.diff(points[:, :2], axis=0), axis=1) > 0.5]
    return points[moved], times[moved]


def resample(points: Array, times: Array, sector_ends: list[float], step: float = STEP_M) -> Track:
    gates = np.array(
        [[np.interp(end, times, points[:, axis]) for axis in (0, 1)] for end in sector_ends]
    )
    closed = np.vstack([points, points[:1]])
    tck, _ = splprep(closed.T, s=len(closed) * 4.0, per=True)
    dense = np.array(splev(np.linspace(0.0, 1.0, DENSE), tck)).T
    arc = np.r_[0.0, np.cumsum(np.linalg.norm(np.diff(dense, axis=0), axis=1))]
    count = int(arc[-1] // step)
    u = np.interp(
        np.linspace(0.0, arc[-1], count, endpoint=False), arc, np.linspace(0.0, 1.0, DENSE)
    )
    shaped = np.array(splev(u, tck)).T
    sectors = [
        float(np.argmin(np.linalg.norm(shaped[:, :2] - gate, axis=1)) * step) for gate in gates
    ]
    shaped[:, :2] -= shaped[:, :2].mean(axis=0)
    shaped[:, 2] -= shaped[:, 2].min()
    return Track(points=shaped, length_m=float(arc[-1]), sectors_m=sectors)


def to_local(lat: Array, lon: Array, lat0: float, lon0: float) -> Array:
    return np.c_[
        (lon - lon0) * np.cos(np.radians(lat0)) * METRES_PER_DEG_LON,
        (lat - lat0) * METRES_PER_DEG_LAT,
    ]


def densify(lines: list[Array], per_segment: int = 12) -> Array:
    out: list[Array] = []
    for line in lines:
        for a, b in pairwise(line):
            for t in np.linspace(0.0, 1.0, per_segment, endpoint=False):
                out.append(a + (b - a) * t)
    return np.array(out)


def _icp(
    source: Array, target: Array, tree: KDTree, rotation: Array, flip: int
) -> tuple[Array, Array]:
    offset = target.mean(axis=0)
    for _ in range(40):
        _, idx = tree.query(source @ rotation.T + offset)
        matched = target[idx]
        mu_s, mu_t = source.mean(axis=0), matched.mean(axis=0)
        u, _, vt = np.linalg.svd((source - mu_s).T @ (matched - mu_t))
        rotation = vt.T @ u.T
        if np.sign(np.linalg.det(rotation)) != flip:
            vt[-1] *= -1
            rotation = vt.T @ u.T
        offset = mu_t - rotation @ mu_s
    return rotation, offset


def align(track_xy: Array, raceway: list[Array]) -> Alignment:
    """Rigid fit of the lap onto the mapped raceway, which may also hold other layouts."""
    lat0, lon0 = (float(value) for value in np.vstack(raceway).mean(axis=0))
    target = densify([to_local(line[:, 0], line[:, 1], lat0, lon0) for line in raceway])
    tree = KDTree(target)
    source = track_xy - track_xy.mean(axis=0)
    best: tuple[float, Array, Array, Array] | None = None
    for flip in (1, -1):
        for degrees in range(0, 360, 5):
            theta = np.radians(degrees)
            start = np.array([[np.cos(theta), -np.sin(theta)], [np.sin(theta), np.cos(theta)]])
            rotation, offset = _icp(source, target, tree, start @ np.diag([1.0, flip]), flip)
            dist, _ = tree.query(source @ rotation.T + offset)
            score = float(np.median(dist))
            if best is None or score < best[0]:
                best = (score, rotation, offset, dist)
    assert best is not None
    score, rotation, offset, dist = best
    return Alignment(
        rotation=rotation,
        offset=offset - rotation @ track_xy.mean(axis=0),
        lat0=lat0,
        lon0=lon0,
        median_m=score,
        p95_m=float(np.percentile(dist, 95)),
    )


def flatten_under(
    xy: Array, heights: Array, track: Array, flat: float = FLAT_M, blend: float = BLEND_M
) -> Array:
    """Ground near the racing line is pulled down to just under it, then eased back."""
    dist, idx = KDTree(track[:, :2]).query(xy)
    weight = np.clip((dist - flat) / blend, 0.0, 1.0)
    return weight * heights + (1 - weight) * (track[idx, 2] - UNDER_TRACK_M)


def inside(points: Array, ring: Array) -> npt.NDArray[np.bool_]:
    """Even-odd rule, vectorised over the points."""
    x, y = points[:, 0][:, None], points[:, 1][:, None]
    ax, ay = ring[:, 0][None, :], ring[:, 1][None, :]
    bx, by = np.roll(ring[:, 0], -1)[None, :], np.roll(ring[:, 1], -1)[None, :]
    crosses = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        at = (bx - ax) * (y - ay) / (by - ay) + ax
    return (crosses & (x < at)).sum(axis=1) % 2 == 1
