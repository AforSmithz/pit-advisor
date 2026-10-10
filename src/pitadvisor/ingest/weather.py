import json
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel

from pitadvisor.ingest import http
from pitadvisor.ingest.ratelimit import Ledger, RateLimiter
from pitadvisor.ingest.raw_store import (
    ObjectStore,
    RawStore,
    write_bronze_by_event,
    write_quarantine,
)
from pitadvisor.quality import contracts
from pitadvisor.types import EventKey, IngestOutcome, Layer, Provenance, Source

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
# 82 members between them. the forecast endpoint's precipitation is one deterministic run
# and its probability comes from a different ensemble, so neither says how likely real rain is
ENSEMBLE_MODELS = ("ecmwf_ifs025", "gfs025")
HOURLY = (
    "temperature_2m",
    "precipitation",
    "precipitation_probability",
    "wind_speed_10m",
    "relative_humidity_2m",
)
# open-meteo only forecasts 16 days out, older dates have to come from the archive
FORECAST_HORIZON = timedelta(days=16)


def is_forecast(race_date: date, today: date) -> bool:
    return today <= race_date <= today + FORECAST_HORIZON


def fetchable(race_date: date, today: date) -> bool:
    # beyond the horizon there is no forecast yet and the archive has nothing either
    return race_date <= today + FORECAST_HORIZON


def endpoint(latitude: float, longitude: float, day: date, forecast: bool) -> str:
    base = FORECAST_URL if forecast else ARCHIVE_URL
    hourly = ",".join(
        HOURLY if forecast else [h for h in HOURLY if h != "precipitation_probability"]
    )
    return (
        f"{base}?latitude={latitude:.4f}&longitude={longitude:.4f}"
        f"&hourly={hourly}&start_date={day.isoformat()}&end_date={day.isoformat()}"
        "&timezone=UTC&wind_speed_unit=kmh"
    )


def ensemble_endpoint(latitude: float, longitude: float, day: date) -> str:
    return (
        f"{ENSEMBLE_URL}?latitude={latitude:.4f}&longitude={longitude:.4f}"
        f"&hourly=precipitation&models={','.join(ENSEMBLE_MODELS)}"
        f"&start_date={day.isoformat()}&end_date={day.isoformat()}&timezone=UTC"
    )


def members(payload: dict[str, Any]) -> dict[str, list[float | None]]:
    hourly = cast(dict[str, Any], payload.get("hourly") or {})
    times = cast(list[str], hourly.get("time") or [])
    # sorted so a member sits at the same index every hour, it is one run through the race
    series = [
        cast(list[float | None], values)
        for name, values in sorted(hourly.items())
        if name.startswith("precipitation")
    ]
    return {
        stamped: [values[index] if index < len(values) else None for values in series]
        for index, stamped in enumerate(times)
    }


def parse(
    payload: dict[str, Any],
    circuit_id: str,
    forecast: bool,
    stamp: dict[str, Any],
    key: EventKey,
    ensemble: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    hourly = cast(dict[str, Any], payload.get("hourly") or {})
    times = cast(list[str], hourly.get("time") or [])
    spread = members(ensemble) if ensemble is not None else {}
    rows: list[dict[str, Any]] = []
    for index, stamped in enumerate(times):
        rows.append(
            {
                **stamp,
                "season": key.season,
                "round": key.round,
                "circuit_id": circuit_id,
                "observed_at": datetime.fromisoformat(stamped).replace(tzinfo=UTC),
                "is_forecast": forecast,
                "temperature_c": _at(hourly, "temperature_2m", index),
                "precipitation_mm": _at(hourly, "precipitation", index),
                "precipitation_probability": _at(hourly, "precipitation_probability", index),
                "wind_speed_kph": _at(hourly, "wind_speed_10m", index),
                "relative_humidity": _at(hourly, "relative_humidity_2m", index),
                "ensemble_precipitation_mm": spread.get(stamped),
            }
        )
    return rows


def _at(hourly: dict[str, Any], field: str, index: int) -> Any:
    values = cast(list[Any] | None, hourly.get(field))
    if values is None or index >= len(values):
        return None
    return values[index]


class WeatherClient:
    def __init__(
        self,
        raw: RawStore,
        ledger: Ledger,
        limiter: RateLimiter | None = None,
        run_id: str = "local",
        fetch: Callable[..., http.Response] | None = None,
    ) -> None:
        self.raw = raw
        self.ledger = ledger
        self.limiter = limiter
        self.run_id = run_id
        self.fetch = fetch or http.fetch

    def snapshot(
        self, key: EventKey, circuit_id: str, latitude: float, longitude: float, day: date
    ) -> tuple[dict[str, Any], str | None, bool]:
        forecast = is_forecast(day, datetime.now(UTC).date())
        url = endpoint(latitude, longitude, day, forecast)
        return self._land(key, f"weather-{'forecast' if forecast else 'archive'}", url)

    def ensemble(
        self, key: EventKey, latitude: float, longitude: float, day: date
    ) -> tuple[dict[str, Any], str | None, bool]:
        return self._land(key, "weather-ensemble", ensemble_endpoint(latitude, longitude, day))

    def _land(self, key: EventKey, name: str, url: str) -> tuple[dict[str, Any], str | None, bool]:
        response = self.fetch(url, self.ledger, self.limiter)
        if response.not_modified:
            cached = self.raw.latest(Source.OPEN_METEO, key, name)
            if cached is not None:
                return json.loads(cached[0]), None, True
            response = self.fetch(url, http.Unconditional(self.ledger), self.limiter)
        uri = self.raw.land(
            key,
            name,
            response.body,
            Provenance(
                run_id=self.run_id,
                source=Source.OPEN_METEO,
                url=url,
                fetched_at=response.fetched_at,
                status=response.status,
                etag=response.etag,
            ),
        )
        return json.loads(response.body), uri, False


def ingest_event(
    client: WeatherClient,
    store: ObjectStore,
    key: EventKey,
    circuit_id: str,
    latitude: float,
    longitude: float,
    day: date,
) -> IngestOutcome:
    stamp = {"run_id": client.run_id, "ingested_at": datetime.now(UTC)}
    payload, uri, cached = client.snapshot(key, circuit_id, latitude, longitude, day)
    forecast = is_forecast(day, datetime.now(UTC).date())
    ensemble: dict[str, Any] | None = None
    uris = [uri] if uri else []
    if forecast:
        ensemble, spread_uri, spread_cached = client.ensemble(key, latitude, longitude, day)
        uris += [spread_uri] if spread_uri else []
        cached = cached and spread_cached
    records = parse(payload, circuit_id, forecast, stamp, key, ensemble)
    kept, dropped = contracts.validate("weather", contracts.WeatherRow, records)
    write_quarantine(store, "weather", key, client.run_id, dropped)
    return IngestOutcome(
        source=Source.OPEN_METEO,
        table="weather",
        season=key.season,
        round=key.round,
        rows=len(kept),
        quarantined=len(dropped),
        raw_objects=uris,
        bronze_objects=write_bronze_by_event(store, "weather", kept),
        requests=2 if forecast else 1,
        not_modified=cached,
    )


class Circuit(BaseModel, frozen=True):
    season: int
    round: int
    circuit_id: str
    latitude: float
    longitude: float
    race_date: date


def event_circuits(store: ObjectStore, season: int, layer: Layer = Layer.BRONZE) -> list[Circuit]:
    from pitadvisor.quality.checks import read_table

    frame = read_table(store, layer, "races")
    if frame is None:
        return []
    rows: list[dict[str, Any]] = frame.to_dicts()
    found = [
        Circuit(
            season=int(row["season"]),
            round=int(row["round"]),
            circuit_id=str(row["circuit_id"]),
            latitude=float(row["latitude"]),
            longitude=float(row["longitude"]),
            race_date=row["race_date"],
        )
        for row in rows
        if int(row["season"]) == season
    ]
    return sorted(found, key=lambda circuit: circuit.round)
