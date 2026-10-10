# polars types every expression argument as IntoExpr, which pyright reads as partly unknown
# pyright: reportUnknownMemberType=false
import json
from datetime import UTC, datetime
from typing import Final, Literal

import numpy as np
import polars as pl
from pydantic import BaseModel

from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.model import backtest, baselines
from pitadvisor.model.metrics import log_loss
from pitadvisor.outputs.view_contracts import ForecastView
from pitadvisor.types import Layer

# views/ is overwritten every run and the bucket expires old versions after 30 days, so what was
# published before each race is kept here under keys that are written once and never again
HISTORY: Final = f"{Layer.VIEWS}/history/"

Kind = Literal["race", "sprint"]


def stage_of(view: ForecastView, kind: Kind) -> str:
    if kind == "sprint":
        return "sprint"
    return "before_qualifying" if view.grid_sampled else "after_qualifying"


def history_key(view: ForecastView, kind: Kind) -> str:
    return (
        f"{HISTORY}season={view.event.season}/round={view.event.round:02d}/"
        f"{kind}-{stage_of(view, kind)}-{view.run_id}.json"
    )


def archive(
    store: ObjectStore, view: ForecastView, kind: Kind, lights_out: datetime | None
) -> str | None:
    """Keeps a forecast only if it was made before the session it forecasts started. A view
    emitted for a past race is a backtest, not a prediction, and would flatter the record."""
    if lights_out is None:
        return None
    if lights_out.tzinfo is None:
        lights_out = lights_out.replace(tzinfo=UTC)
    if view.generated_at >= lights_out:
        return None
    key = history_key(view, kind)
    if store.exists(key):
        return None
    store.put(key, json.dumps(view.model_dump(mode="json"), indent=2, allow_nan=False).encode())
    return key


class PublishedScore(BaseModel, frozen=True):
    season: int
    round: int
    kind: str
    stage: str
    run_id: str
    generated_at: datetime
    drivers: int
    # multiclass log loss over the classified finishing place, the backtest's primary score, for
    # the published forecast and for each baseline fitted on what was known before the race
    log_loss: dict[str, float]


def _published(store: ObjectStore) -> list[tuple[Kind, ForecastView]]:
    found: list[tuple[Kind, ForecastView]] = []
    for item in store.list(HISTORY):
        name = item.key.rsplit("/", 1)[-1]
        kind: Kind = "sprint" if name.startswith("sprint-") else "race"
        found.append((kind, ForecastView.model_validate_json(store.get(item.key))))
    return found


def _field(
    pane: backtest.Panel, view: ForecastView, kind: Kind, sprints: pl.DataFrame
) -> tuple[dict[str, int], baselines.Entries | None]:
    season, round_ = view.event.season, view.event.round
    if kind == "race":
        rows = pane.results.filter((pl.col("season") == season) & (pl.col("round") == round_))
        field = pane.entries.get((season, round_))
    else:
        rows = sprints.filter((pl.col("season") == season) & (pl.col("round") == round_))
        before = pane.results.select(baselines.COLUMNS).filter(pl.col("race_date") < view.as_of)
        try:
            field = baselines.entries(
                pl.concat([before, rows.select(baselines.COLUMNS)]), season, round_
            )
        except baselines.NoHistoryError:
            field = None
    finished = {
        str(row["driver_code"]): int(row["position"])
        for row in rows.drop_nulls(["driver_code", "position"]).iter_rows(named=True)
    }
    return finished, field


def score(store: ObjectStore, pane: backtest.Panel, sprints: pl.DataFrame) -> list[PublishedScore]:
    """Every archived forecast whose session has a result, scored the way the backtest scores a
    race. The grid baseline only stands beside a forecast made once the grid was known."""
    scored: list[PublishedScore] = []
    for kind, view in _published(store):
        finished, field = _field(pane, view, kind, sprints)
        drivers = [driver for driver in view.drivers if driver.driver_code in finished]
        if len(drivers) < 2:
            continue
        width = len(view.drivers[0].position)
        starters = len(view.drivers)
        actual = np.array([finished[driver.driver_code] for driver in drivers]) - 1
        mine = backtest.renormalised(
            np.array([driver.position for driver in drivers]), starters, width
        )
        losses = {backtest.MODEL: log_loss(mine, actual)}
        if field is not None:
            order = {code: slot for slot, code in enumerate(field.driver_code)}
            if all(driver.driver_code in order for driver in drivers):
                fitted = baselines.fit(
                    pane.results.select(baselines.COLUMNS),
                    view.event.race_date if kind == "race" else view.as_of,
                    known=pane.entries,
                    width=field.width,
                )
                rows = [order[driver.driver_code] for driver in drivers]
                stage = stage_of(view, kind)
                for name, values in fitted.predict(field).items():
                    if name == "grid" and stage == "before_qualifying":
                        continue
                    losses[name] = log_loss(
                        backtest.renormalised(values[rows], starters, width), actual
                    )
        scored.append(
            PublishedScore(
                season=view.event.season,
                round=view.event.round,
                kind=kind,
                stage=stage_of(view, kind),
                run_id=view.run_id,
                generated_at=view.generated_at,
                drivers=len(drivers),
                log_loss=losses,
            )
        )
    return sorted(scored, key=lambda item: (item.season, item.round, item.kind, item.generated_at))


def summarise(scores: list[PublishedScore]) -> str:
    if not scores:
        return "nothing published has a result yet\n"
    lines = ["published forecasts, scored after the fact against what happened", ""]
    for item in scores:
        rest = "  ".join(f"{name} {value:.4f}" for name, value in sorted(item.log_loss.items()))
        lines.append(
            f"{item.season}:{item.round:<3} {item.kind:<6} {item.stage:<18} "
            f"{item.generated_at:%Y-%m-%d %H:%M}  {rest}"
        )
    lines.append("")
    by_stage: dict[tuple[str, str], list[PublishedScore]] = {}
    for item in scores:
        by_stage.setdefault((item.kind, item.stage), []).append(item)
    for (kind, stage), items in sorted(by_stage.items()):
        names = sorted({name for item in items for name in item.log_loss})
        means = "  ".join(
            f"{name} {np.mean([i.log_loss[name] for i in items if name in i.log_loss]):.4f}"
            for name in names
        )
        lines.append(f"{kind} {stage}: {len(items)} forecasts, mean  {means}")
    return "\n".join(lines) + "\n"
