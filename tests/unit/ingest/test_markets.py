import json
from datetime import UTC, date, datetime

import numpy as np
import polars as pl
import pytest

from pitadvisor.ingest import markets
from pitadvisor.ingest.raw_store import LocalObjectStore
from pitadvisor.quality.checks import read_table
from pitadvisor.types import EventKey, Layer

START = datetime(2025, 8, 3, 13, 0, tzinfo=UTC)


def candle(at: datetime, bid: str | None, ask: str | None, price: str | None = None) -> dict:
    return {
        "end_period_ts": int(at.timestamp()),
        "yes_bid": {"close": bid},
        "yes_ask": {"close": ask},
        "price": {"close": price},
    }


def test_the_kalshi_price_is_the_last_midpoint_an_hour_before_the_start():
    cutoff = START - markets.LEAD
    payload = {
        "candlesticks": [
            candle(datetime(2025, 8, 3, 10, tzinfo=UTC), "0.40", "0.44"),
            candle(datetime(2025, 8, 3, 12, tzinfo=UTC), "0.50", "0.54"),
            # after the cutoff, the race is about to start
            candle(datetime(2025, 8, 3, 12, 30, tzinfo=UTC), "0.90", "0.92"),
        ]
    }
    read = markets.kalshi_rows(payload, "candles-KXF1RACE-HUNGP25-VER", cutoff)
    assert read is not None
    assert read["price"] == pytest.approx(0.52)
    assert read["market"] == "win"
    assert read["driver"] == "VER"
    podium = markets.kalshi_rows(payload, "candles-KXF1RACEPODIUM-HUNGP25-VER", cutoff)
    assert podium is not None
    assert podium["market"] == "podium"


def test_a_candle_with_no_ask_falls_back_to_the_last_trade():
    cutoff = START - markets.LEAD
    payload = {"candlesticks": [candle(datetime(2025, 8, 3, 10, tzinfo=UTC), "0", None, "0.3")]}
    read = markets.kalshi_rows(payload, "candles-KXF1RACE-HUNGP25-NOR", cutoff)
    assert read is not None
    assert read["price"] == pytest.approx(0.3)


def test_the_polymarket_price_is_the_last_trade_before_the_cutoff():
    cutoff = START - markets.LEAD
    payload = {
        "history": [
            {"t": int(datetime(2025, 8, 3, 9, tzinfo=UTC).timestamp()), "p": 0.2},
            {"t": int(datetime(2025, 8, 3, 11, tzinfo=UTC).timestamp()), "p": 0.25},
            {"t": int(datetime(2025, 8, 3, 13, tzinfo=UTC).timestamp()), "p": 0.9},
        ]
    }
    read = markets.polymarket_rows(payload, "prices-win-max-verstappen", cutoff)
    assert read is not None
    assert read["price"] == pytest.approx(0.25)
    assert read["driver"] == "max-verstappen"


FIELD = pl.DataFrame(
    {"driver_code": ["VER", "NOR", "ANT"], "driver_id": ["max_verstappen", "norris", "antonelli"]}
)


def test_a_driver_is_resolved_by_code_on_kalshi_and_by_name_on_polymarket():
    assert markets.resolve("VER", "kalshi", FIELD) == "VER"
    assert markets.resolve("XXX", "kalshi", FIELD) is None
    assert markets.resolve("max-verstappen", "polymarket", FIELD) == "VER"
    assert markets.resolve("kimi-antonelli", "polymarket", FIELD) == "ANT"
    assert markets.resolve("lewis-hamilton", "polymarket", FIELD) is None


def test_a_market_settled_the_night_after_still_finds_its_race():
    schedule = pl.DataFrame(
        {
            "season": [2025, 2025],
            "round": [14, 15],
            "race_date": [date(2025, 8, 3), date(2025, 8, 31)],
        }
    )
    assert markets.race_for(schedule, date(2025, 8, 4)) == (2025, 14)
    assert markets.race_for(schedule, date(2025, 8, 20)) is None


def test_landed_prices_become_bronze_rows(tmp_path):
    store = LocalObjectStore(tmp_path)
    schedule = pl.DataFrame(
        {"season": [2025], "round": [14], "race_date": [date(2025, 8, 3)], "start_utc": [START]}
    )
    results = FIELD.with_columns(pl.lit(2025).alias("season"), pl.lit(14).alias("round"))
    body = json.dumps(
        {"candlesticks": [candle(datetime(2025, 8, 3, 10, tzinfo=UTC), "0.5", "0.54")]}
    ).encode()
    kept, dropped = markets.materialize(
        store,
        [("raw/k.json", EventKey(season=2025, round=14), "candles-KXF1RACE-HUNGP25-VER", body)],
        schedule,
        results,
        {"run_id": "r", "ingested_at": datetime(2026, 10, 10, tzinfo=UTC)},
    )
    assert (kept, dropped) == (1, 0)
    frame = read_table(store, Layer.BRONZE, "market_odds")
    assert frame is not None
    assert frame["driver_code"].to_list() == ["VER"]
    assert np.isclose(frame["price"][0], 0.52)
