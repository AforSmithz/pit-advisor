# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import json
import re
import unicodedata
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, cast
from urllib.parse import urlencode

import polars as pl

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import (
    ObjectStore,
    RawStore,
    write_bronze_by_event,
    write_quarantine,
)
from pitadvisor.quality import contracts
from pitadvisor.types import EventKey, Provenance, Source

KALSHI: Final = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA: Final = "https://gamma-api.polymarket.com"
CLOB: Final = "https://clob.polymarket.com"
SERIES: Final = {"KXF1RACE": "win", "KXF1RACEPODIUM": "podium"}
# the forecast a market is compared with is made the evening before, so the price read is the
# last one at least this long before the lights go out
LEAD: Final = timedelta(hours=1)
WINDOW: Final = timedelta(hours=30)
WINNER: Final = re.compile(r"grand prix\W+(driver\W+)?winner", re.I)
EXCLUDED: Final = re.compile(r"pole|sprint|constructor|fastest|podium", re.I)
QUESTION: Final = re.compile(r"will (.+?) win", re.I)

Fetch = Callable[..., http.Response]


def fold(name: str) -> str:
    stripped = "".join(
        char for char in unicodedata.normalize("NFKD", name) if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "-", stripped.lower()).strip("-")


def race_for(schedule: pl.DataFrame, when: date) -> tuple[int, int] | None:
    """A market settles on race day, or the night after it in some time zones."""
    near = schedule.filter(
        (pl.col("race_date") >= when - timedelta(days=1))
        & (pl.col("race_date") <= when + timedelta(days=1))
    )
    if near.height != 1:
        return None
    return int(near["season"][0]), int(near["round"][0])


class Client:
    def __init__(
        self,
        raw: RawStore,
        ledger: Ledger,
        limiters: dict[Source, RateLimiter | None],
        run_id: str,
        fetch: Fetch = http.fetch,
    ) -> None:
        self.raw = raw
        self.ledger = ledger
        self.limiters = limiters
        self.run_id = run_id
        self.fetch = fetch

    def get(self, source: Source, url: str) -> tuple[Any, bytes, http.Response]:
        # conditional gets would return 304 against bytes that may not be in raw, and a
        # settled market never changes, so every read is a plain one
        response = self.fetch(url, http.Unconditional(self.ledger), self.limiters.get(source))
        return json.loads(response.body), response.body, response

    def land(
        self, source: Source, key: EventKey, name: str, body: bytes, response: http.Response
    ) -> str:
        return self.raw.land(
            key,
            name,
            body,
            Provenance(
                run_id=self.run_id,
                source=source,
                url=response.url,
                fetched_at=response.fetched_at,
                status=response.status,
                etag=response.etag,
            ),
        )


def kalshi(client: Client, schedule: pl.DataFrame) -> list[str]:
    landed: list[str] = []
    for series in SERIES:
        query = urlencode({"series_ticker": series, "status": "settled", "limit": 200})
        listed, _, _ = client.get(Source.KALSHI, f"{KALSHI}/events?{query}")
        for event in listed.get("events", []):
            ticker = str(event["event_ticker"])
            markets = _kalshi_markets(client, ticker)
            if not markets:
                continue
            settled = str(markets[0].get("expected_expiration_time") or "")
            if not settled:
                continue
            when = datetime.fromisoformat(settled.replace("Z", "+00:00"))
            found = race_for(schedule, when.date())
            if found is None:
                continue
            key = EventKey(season=found[0], round=found[1])
            start = int((when - WINDOW).timestamp())
            stop = int((when + timedelta(hours=6)).timestamp())
            for market in markets:
                name = str(market["ticker"])
                query = urlencode({"start_ts": start, "end_ts": stop, "period_interval": 60})
                url = f"{KALSHI}/historical/markets/{name}/candlesticks?{query}"
                try:
                    _, body, response = client.get(Source.KALSHI, url)
                except http.FetchError:
                    url = f"{KALSHI}/series/{series}/markets/{name}/candlesticks?{query}"
                    _, body, response = client.get(Source.KALSHI, url)
                landed.append(client.land(Source.KALSHI, key, f"candles-{name}", body, response))
    return landed


def _kalshi_markets(client: Client, event: str) -> list[dict[str, Any]]:
    for path in ("markets", "historical/markets"):
        try:
            found, _, _ = client.get(
                Source.KALSHI, f"{KALSHI}/{path}?{urlencode({'event_ticker': event, 'limit': 100})}"
            )
        except http.FetchError:
            continue
        markets = cast(list[dict[str, Any]], found.get("markets", []))
        if markets:
            return markets
    return []


def polymarket(client: Client, schedule: pl.DataFrame) -> list[str]:
    landed: list[str] = []
    offset = 0
    events: list[dict[str, Any]] = []
    while True:
        query = urlencode({"tag_slug": "f1", "closed": "true", "limit": 100, "offset": offset})
        page, _, _ = client.get(Source.POLYMARKET, f"{GAMMA}/events?{query}")
        if not page:
            break
        events.extend(cast(list[dict[str, Any]], page))
        offset += 100
    for event in events:
        title = str(event.get("title", ""))
        if not WINNER.search(title) or EXCLUDED.search(title):
            continue
        ending = str(event.get("endDate") or "")
        if not ending:
            continue
        when = datetime.fromisoformat(ending.replace("Z", "+00:00"))
        found = race_for(schedule, when.date())
        if found is None:
            continue
        key = EventKey(season=found[0], round=found[1])
        start = int((when - WINDOW - timedelta(days=1)).timestamp())
        stop = int((when + timedelta(hours=6)).timestamp())
        for market in event.get("markets", []):
            asked = QUESTION.search(str(market.get("question", "")))
            tokens = json.loads(str(market.get("clobTokenIds") or "[]"))
            if asked is None or not tokens:
                continue
            driver = fold(asked.group(1))
            query = urlencode(
                {"market": tokens[0], "startTs": start, "endTs": stop, "fidelity": 60}
            )
            _, body, response = client.get(Source.POLYMARKET, f"{CLOB}/prices-history?{query}")
            landed.append(
                client.land(Source.POLYMARKET, key, f"prices-win-{driver}", body, response)
            )
    return landed


def kalshi_rows(payload: dict[str, Any], name: str, cutoff: datetime) -> dict[str, Any] | None:
    """`name` is the landed object, candles-<series>-<event>-<driver code>."""
    parts = name.removeprefix("candles-").split("-")
    if len(parts) < 3 or parts[0] not in SERIES:
        return None
    best: tuple[int, float] | None = None
    for candle in payload.get("candlesticks", []):
        ended = int(candle.get("end_period_ts", 0))
        if ended > cutoff.timestamp():
            continue
        bid = _number(candle.get("yes_bid", {}).get("close"))
        ask = _number(candle.get("yes_ask", {}).get("close"))
        price = _number(candle.get("price", {}).get("close"))
        value = (bid + ask) / 2.0 if bid is not None and ask is not None and ask > 0 else price
        if value is None:
            continue
        if best is None or ended > best[0]:
            best = (ended, value)
    if best is None:
        return None
    return {
        "source": str(Source.KALSHI),
        "market": SERIES[parts[0]],
        "driver": parts[-1],
        "snapshot_utc": datetime.fromtimestamp(best[0], UTC),
        "price": best[1],
    }


def polymarket_rows(payload: dict[str, Any], name: str, cutoff: datetime) -> dict[str, Any] | None:
    driver = name.removeprefix("prices-win-")
    best: tuple[int, float] | None = None
    for point in payload.get("history", []):
        at = int(point.get("t", 0))
        if at > cutoff.timestamp():
            continue
        if best is None or at > best[0]:
            best = (at, float(point.get("p", 0.0)))
    if best is None:
        return None
    return {
        "source": str(Source.POLYMARKET),
        "market": "win",
        "driver": driver,
        "snapshot_utc": datetime.fromtimestamp(best[0], UTC),
        "price": best[1],
    }


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def resolve(driver: str, source: str, field: pl.DataFrame) -> str | None:
    """Kalshi names the driver by his code; Polymarket by name, matched on the jolpica id."""
    codes = {str(code) for code in field["driver_code"].to_list()}
    if source == str(Source.KALSHI):
        return driver if driver in codes else None
    words = driver.split("-")
    for row in field.iter_rows(named=True):
        ident = fold(str(row["driver_id"]))
        if words and (words[-1] in ident.split("-") or ident == driver):
            return str(row["driver_code"])
    return None


def materialize(
    store: ObjectStore,
    objects: list[tuple[str, EventKey, str, bytes]],
    schedule: pl.DataFrame,
    results: pl.DataFrame,
    stamp: dict[str, Any],
) -> tuple[int, int]:
    """objects are (raw key, event, name, body). Each becomes at most one bronze row."""
    records: list[dict[str, Any]] = []
    for raw_key, key, name, body in objects:
        race = schedule.filter((pl.col("season") == key.season) & (pl.col("round") == key.round))
        if not race.height or race["start_utc"][0] is None:
            continue
        cutoff = cast(datetime, race["start_utc"][0]) - LEAD
        payload = json.loads(body)
        read = (
            kalshi_rows(payload, name, cutoff)
            if name.startswith("candles-")
            else polymarket_rows(payload, name, cutoff)
        )
        if read is None:
            continue
        field = results.filter((pl.col("season") == key.season) & (pl.col("round") == key.round))
        code = resolve(str(read["driver"]), str(read["source"]), field)
        if code is None:
            continue
        records.append(
            {
                **stamp,
                "season": key.season,
                "round": key.round,
                "source": read["source"],
                "market": read["market"],
                "driver_code": code,
                "snapshot_utc": read["snapshot_utc"],
                "price": read["price"],
                "raw_key": raw_key,
            }
        )
    grouped: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for record in records:
        grouped.setdefault((int(record["season"]), int(record["round"])), []).append(record)
    kept_total = dropped_total = 0
    for (season, round_), rows in sorted(grouped.items()):
        kept, dropped = contracts.validate("market_odds", contracts.MarketOddsRow, rows)
        write_quarantine(
            store,
            "market_odds",
            EventKey(season=season, round=round_),
            str(stamp["run_id"]),
            dropped,
        )
        if kept:
            write_bronze_by_event(store, "market_odds", kept)
        kept_total += len(kept)
        dropped_total += len(dropped)
    return kept_total, dropped_total
