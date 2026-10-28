# scipy.ndimage ships no stubs, so map_coordinates reads as partly unknown
# pyright: reportUnknownVariableType=false
import gzip
import math
import time
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Final

import numpy as np
import numpy.typing as npt
from scipy.ndimage import map_coordinates

from pitadvisor.ingest.http import USER_AGENT
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Provenance, Source

BASE: Final = "https://s3.amazonaws.com/elevation-tiles-prod/skadi"
VOID: Final = -32768
TIMEOUT: Final = 120.0
ATTEMPTS: Final = 4

Opener = Callable[[urllib.request.Request, float], Any]
Grid = npt.NDArray[np.float64]


def _open(request: urllib.request.Request, timeout: float) -> Any:
    return urllib.request.urlopen(request, timeout=timeout)


def tile_name(lat: int, lon: int) -> str:
    return f"{'N' if lat >= 0 else 'S'}{abs(lat):02d}{'E' if lon >= 0 else 'W'}{abs(lon):03d}"


def tile_url(name: str) -> str:
    return f"{BASE}/{name[:3]}/{name}.hgt.gz"


def tiles_for(south: float, west: float, north: float, east: float) -> list[tuple[int, int]]:
    return [
        (lat, lon)
        for lat in range(math.floor(south), math.floor(north) + 1)
        for lon in range(math.floor(west), math.floor(east) + 1)
    ]


def decode(body: bytes) -> Grid:
    data = gzip.decompress(body)
    side = math.isqrt(len(data) // 2)
    if side * side * 2 != len(data):
        raise ValueError(f"a skadi tile is a square of int16, got {len(data)} bytes")
    grid = np.frombuffer(data, ">i2").reshape(side, side).astype(np.float64)
    grid[grid == VOID] = np.nan
    return grid


def _download(
    request: urllib.request.Request, opener: Opener, sleep: Callable[[float], None]
) -> bytes:
    for attempt in range(ATTEMPTS - 1):
        try:
            with opener(request, TIMEOUT) as response:
                return bytes(response.read())
        except OSError:
            sleep(2.0**attempt)
    with opener(request, TIMEOUT) as response:
        return bytes(response.read())


def fetch(
    raw: RawStore,
    key: EventKey,
    lat: int,
    lon: int,
    run_id: str,
    opener: Opener = _open,
    sleep: Callable[[float], None] = time.sleep,
) -> bytes:
    name = tile_name(lat, lon)
    held = raw.latest(Source.TERRAIN_TILES, key, name)
    if held is not None:
        return held[0]
    request = urllib.request.Request(tile_url(name), headers={"User-Agent": USER_AGENT})
    body = _download(request, opener, sleep)
    raw.land(
        key,
        name,
        body,
        Provenance(
            run_id=run_id,
            source=Source.TERRAIN_TILES,
            url=tile_url(name),
            fetched_at=datetime.now(UTC),
            status=200,
        ),
        suffix="hgt.gz",
    )
    return body


class Terrain:
    def __init__(self, tiles: dict[tuple[int, int], Grid]) -> None:
        self.tiles = tiles

    def sample(self, lat: Grid, lon: Grid) -> Grid:
        out = np.full(lat.shape, np.nan)
        for (south, west), grid in self.tiles.items():
            inside = (lat >= south) & (lat < south + 1) & (lon >= west) & (lon < west + 1)
            if not inside.any():
                continue
            last = grid.shape[0] - 1
            # row 0 is the tile's north edge and column 0 its west edge
            rows = (south + 1 - lat[inside]) * last
            cols = (lon[inside] - west) * last
            out[inside] = map_coordinates(grid, [rows, cols], order=1, mode="nearest")
        return out


# tilezen's per-region sources, quoted from its attribution document; srtm covers the rest
REGIONAL_CREDIT: Final = {
    "Mexico": "INEGI, Continental relief, 2016",
    "USA": "3DEP data courtesy of the U.S. Geological Survey",
    "Canada": "contains information licensed under the Open Government Licence - Canada",
    "Australia": "© Commonwealth of Australia (Geoscience Australia) 2017",
}
EU_DEM: Final = frozenset(
    {
        "Austria",
        "Belgium",
        "France",
        "Hungary",
        "Italy",
        "Monaco",
        "Netherlands",
        "Portugal",
        "Spain",
        "UK",
    }
)


def credit(country: str) -> str:
    regional = REGIONAL_CREDIT.get(country) or ("EU-DEM, Copernicus" if country in EU_DEM else None)
    shared = "SRTM data courtesy of the U.S. Geological Survey"
    return f"Terrain Tiles on AWS Open Data: {regional + ', ' if regional else ''}{shared}"
