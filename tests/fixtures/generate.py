"""Regenerates the synthetic payloads the ingest tests run against. No real data."""

import json
from pathlib import Path

ROOT = Path(__file__).parent
DRIVERS = [
    ("verstappen", "red_bull", 1),
    ("norris", "mclaren", 4),
    ("leclerc", "ferrari", 16),
]


def envelope(inner: dict, total: int = 1) -> dict:
    return {
        "MRData": {
            "limit": "100",
            "offset": "0",
            "total": str(total),
            "RaceTable": {"season": "2024", "Races": [inner]},
        }
    }


def race_shell(extra: dict) -> dict:
    return {
        "season": "2024",
        "round": "5",
        "raceName": "Synthetic Grand Prix",
        "Circuit": {
            "circuitId": "synthetica",
            "circuitName": "Synthetica Ring",
            "Location": {
                "lat": "1.2914",
                "long": "103.8640",
                "locality": "Testville",
                "country": "Nowhere",
            },
        },
        "date": "2024-05-05",
        "time": "13:00:00Z",
        **extra,
    }


def races() -> dict:
    return envelope(race_shell({}))


def results() -> dict:
    rows = [
        {
            "number": str(number),
            "position": str(index + 1),
            "positionText": str(index + 1),
            "points": str(25 - index * 7),
            "Driver": {"driverId": driver},
            "Constructor": {"constructorId": team},
            "grid": str(index + 1),
            "laps": "57",
            "status": "Finished",
            "Time": {"millis": str(5400000 + index * 4000), "time": "1:30:00.000"},
            "FastestLap": {"rank": str(index + 1), "lap": "44", "Time": {"time": "1:32.608"}},
        }
        for index, (driver, team, number) in enumerate(DRIVERS)
    ]
    return envelope(race_shell({"Results": rows}))


def qualifying() -> dict:
    rows = [
        {
            "number": str(number),
            "position": str(index + 1),
            "Driver": {"driverId": driver},
            "Constructor": {"constructorId": team},
            "Q1": "1:31.500",
            "Q2": "1:30.900",
            "Q3": f"1:30.{100 + index * 25}",
        }
        for index, (driver, team, number) in enumerate(DRIVERS)
    ]
    return envelope(race_shell({"QualifyingResults": rows}))


def laps() -> dict:
    numbered = [
        {
            "number": str(lap),
            "Timings": [
                {
                    "driverId": driver,
                    "position": str(index + 1),
                    "time": f"1:33.{400 + index * 30 + lap}",
                }
                for index, (driver, _, _) in enumerate(DRIVERS)
            ],
        }
        for lap in (1, 2, 3)
    ]
    return envelope(race_shell({"Laps": numbered}))


def pitstops() -> dict:
    stops = [
        {
            "driverId": driver,
            "lap": str(18 + index),
            "stop": "1",
            "time": f"14:0{index}:30",
            "duration": f"2{index}.315",
        }
        for index, (driver, _, _) in enumerate(DRIVERS)
    ]
    return envelope(race_shell({"PitStops": stops}))


def open_meteo() -> dict:
    hours = [f"2024-05-05T{hour:02d}:00" for hour in range(12, 16)]
    return {
        "latitude": 1.2914,
        "longitude": 103.864,
        "hourly": {
            "time": hours,
            "temperature_2m": [30.1, 30.6, 31.0, 30.4],
            "precipitation": [0.0, 0.0, 1.4, 0.2],
            "precipitation_probability": [5, 12, 60, 30],
            "wind_speed_10m": [8.2, 9.1, 14.0, 11.3],
            "relative_humidity_2m": [70, 72, 81, 78],
        },
    }


BUILDERS = {
    "jolpica/races.json": races,
    "jolpica/results.json": results,
    "jolpica/qualifying.json": qualifying,
    "jolpica/laps.json": laps,
    "jolpica/pitstops.json": pitstops,
    "open_meteo/forecast.json": open_meteo,
}


def main() -> None:
    for name, builder in BUILDERS.items():
        path = ROOT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(builder(), indent=2) + "\n")


if __name__ == "__main__":
    main()
