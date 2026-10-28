import gzip
import io

import numpy as np
import pytest

from pitadvisor.ingest import terrain
from pitadvisor.ingest.raw_store import RawStore
from pitadvisor.types import EventKey, Source

KEY = EventKey(season=2025, round=13)


def tile(grid):
    return gzip.compress(np.asarray(grid, dtype=">i2").tobytes())


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_tile_names_follow_the_southwest_corner():
    assert terrain.tile_name(19, -100) == "N19W100"
    assert terrain.tile_name(50, 5) == "N50E005"
    assert terrain.tile_name(-38, 144) == "S38E144"
    assert terrain.tile_url("N19W100").endswith("/skadi/N19/N19W100.hgt.gz")


def test_a_box_across_a_degree_line_needs_both_tiles():
    assert terrain.tiles_for(19.39, -99.1, 19.41, -99.07) == [(19, -100)]
    assert terrain.tiles_for(49.99, 5.95, 50.01, 5.98) == [(49, 5), (50, 5)]


def test_decode_reads_big_endian_and_blanks_voids():
    grid = terrain.decode(tile([[100, -32768, 3], [4, 5, 6], [7, 8, 9]]))
    assert grid.shape == (3, 3)
    assert grid[0, 0] == 100
    assert np.isnan(grid[0, 1])


def test_decode_refuses_a_tile_that_is_not_square():
    with pytest.raises(ValueError, match="square of int16"):
        terrain.decode(gzip.compress(b"\x00\x01" * 6))


def test_sampling_puts_the_first_row_on_the_north_edge():
    grid = np.array([[0.0, 10.0], [20.0, 30.0]])
    sampled = terrain.Terrain({(19, -100): grid}).sample(
        np.array([19.999999, 19.0, 19.5, 30.0]), np.array([-100.0, -99.000001, -99.5, 0.0])
    )
    assert sampled[0] == pytest.approx(0.0, abs=1e-3)
    assert sampled[1] == pytest.approx(30.0, abs=1e-3)
    assert sampled[2] == pytest.approx(15.0)
    assert np.isnan(sampled[3])


def test_the_credit_names_the_regional_source_where_tilezen_has_one():
    assert "INEGI" in terrain.credit("Mexico")
    assert "EU-DEM" in terrain.credit("Belgium")
    assert terrain.credit("Japan").endswith("SRTM data courtesy of the U.S. Geological Survey")


def test_a_tile_lands_in_raw_once(store):
    raw = RawStore(store)
    body = tile([[1, 2], [3, 4]])
    calls = []

    def opener(request, timeout):
        calls.append(request.full_url)
        return Reply(body)

    assert terrain.fetch(raw, KEY, 50, 5, "run-1", opener) == body
    assert terrain.fetch(raw, KEY, 50, 5, "run-1", opener) == body
    assert len(calls) == 1
    (landed,) = raw.versions(Source.TERRAIN_TILES, KEY, "N50E005")
    assert landed.endswith(".hgt.gz")


def test_a_dropped_download_is_retried():
    body = tile([[1, 2], [3, 4]])
    answers = [OSError("reset"), body]

    def opener(request, timeout):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return Reply(answer)

    waits = []
    assert terrain._download(object(), opener, waits.append) == body
    assert waits == [1.0]
