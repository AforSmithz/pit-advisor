from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml
from pydantic import BaseModel, Field

TAXONOMY = Path("data/reference/circuits.yml")
DEMANDS = ("downforce", "traction", "braking", "top_speed", "abrasion", "kerbs")
FEATURES = ("length_km", "corners", "altitude_m", *DEMANDS)


class Demand(BaseModel, frozen=True):
    downforce: int = Field(ge=1, le=5)
    traction: int = Field(ge=1, le=5)
    braking: int = Field(ge=1, le=5)
    top_speed: int = Field(ge=1, le=5)
    abrasion: int = Field(ge=1, le=5)
    kerbs: int = Field(ge=1, le=5)


class CircuitProfile(BaseModel, frozen=True):
    circuit_id: str
    length_km: float = Field(gt=2.0, lt=8.0)
    corners: int = Field(ge=8, le=30)
    direction: Literal["clockwise", "anticlockwise"]
    altitude_m: int = Field(ge=-50, le=3000)
    # the first season of the current layout, when it changed inside our window
    reprofiled: int | None = None
    demand: Demand


def load(path: Path = TAXONOMY) -> dict[str, CircuitProfile]:
    rows: dict[str, dict[str, Any]] = yaml.safe_load(path.read_text())
    return {
        circuit_id: CircuitProfile(circuit_id=circuit_id, **row) for circuit_id, row in rows.items()
    }


def vector(profile: CircuitProfile) -> np.ndarray:
    demand = profile.demand
    return np.array(
        [
            profile.length_km,
            profile.corners,
            profile.altitude_m,
            *[getattr(demand, d) for d in DEMANDS],
        ],
        dtype=float,
    )


def matrix(profiles: dict[str, CircuitProfile]) -> tuple[list[str], np.ndarray]:
    """Z-scored, because altitude spans 2240 m and a demand band spans 4."""
    ids = sorted(profiles)
    raw = np.vstack([vector(profiles[circuit_id]) for circuit_id in ids])
    spread = raw.std(axis=0)
    spread[spread == 0] = 1.0
    return ids, (raw - raw.mean(axis=0)) / spread


def comparable_from(profile: CircuitProfile) -> int | None:
    return profile.reprofiled
