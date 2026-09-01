import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Final, Protocol, TypeVar, cast

import duckdb
from pydantic import BaseModel, Field

from pitadvisor.agent.sql_guard import RejectedError, guard
from pitadvisor.config import Settings, boto_session
from pitadvisor.ingest.raw_store import ObjectStore
from pitadvisor.outputs.view_contracts import (
    CalibrationView,
    DriverView,
    ForecastView,
    TrackView,
    WeekendView,
    view_key,
)
from pitadvisor.types import Source

MAX_SIM_PATHS: Final = 2000
# every interval in the project is a 95% one: z of 1.96 in the feature fits, t at 0.975 in the
# pace fit, and 0.95 in the metrics. the tools say so, so an answer that says so is grounded
INTERVAL_LEVEL: Final = 0.95
# a passage a tool returned is a tool result, and a figure quoted out of one came from a tool
IN_TEXT = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
# "turns 11-14" is two numbers and a range, not eleven and minus fourteen
RANGE = re.compile(r"(?<=\d)\s*[-\u2013\u2014\u2212]\s*(?=\d)")
MARKETS: Final = ("win", "podium", "points", "finish", "expected_position")
CORPUS_SOURCES: Final = (str(Source.FIA_DOCS), str(Source.WIKIPEDIA), str(Source.CURATED))
LOCAL_MARTS: Final = Path("data/local/pitadvisor.duckdb")

View = TypeVar("View", bound=BaseModel)


class ToolError(RuntimeError):
    """Raised when a tool cannot answer. Never a substitute for a number."""


class ToolResult(BaseModel, frozen=True):
    tool: str
    ok: bool
    payload: dict[str, Any] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
    detail: str = ""


class Marts(Protocol):
    def rows(self, sql: str) -> list[dict[str, Any]]: ...


class Retriever(Protocol):
    def retrieve(self, query: str, top_k: int, source: str | None) -> list[dict[str, Any]]: ...


class Simulator(Protocol):
    def simulate(self, event: str, scenario: str, paths: int) -> dict[str, Any]: ...


class DuckDBMarts:
    """The dbt local target, which is a real build of the same models Athena runs."""

    def __init__(self, path: Path = LOCAL_MARTS) -> None:
        self.path = path

    def rows(self, sql: str) -> list[dict[str, Any]]:
        if not self.path.exists():
            raise ToolError(f"{self.path} is not there, run dbt build --target local first")
        connection = duckdb.connect(str(self.path), read_only=True)
        try:
            result = connection.sql(sql)
            names = list(result.columns)
            return [dict(zip(names, row, strict=True)) for row in result.fetchall()]
        finally:
            connection.close()


class AthenaMarts:
    def __init__(
        self,
        client: Any,
        database: str,
        workgroup: str,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.client = client
        self.database = database
        self.workgroup = workgroup
        self.timeout_seconds = timeout_seconds
        self.sleep = sleep

    def rows(self, sql: str) -> list[dict[str, Any]]:
        started: dict[str, Any] = self.client.start_query_execution(
            QueryString=sql,
            QueryExecutionContext={"Database": self.database},
            WorkGroup=self.workgroup,
        )
        query_id = str(started["QueryExecutionId"])
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            execution: dict[str, Any] = self.client.get_query_execution(QueryExecutionId=query_id)[
                "QueryExecution"
            ]
            state = str(execution["Status"]["State"])
            if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                break
            if time.monotonic() > deadline:
                raise ToolError(f"athena query {query_id} still {state} after the timeout")
            self.sleep(0.5)
        if state != "SUCCEEDED":
            reason = execution["Status"].get("StateChangeReason", state)
            raise ToolError(f"athena rejected the query: {reason}")
        return self._rows_of(query_id)

    def _rows_of(self, query_id: str) -> list[dict[str, Any]]:
        page: dict[str, Any] = self.client.get_query_results(QueryExecutionId=query_id)
        info = page["ResultSet"]["ResultSetMetadata"]["ColumnInfo"]
        names = [str(column["Name"]) for column in info]
        types = [str(column["Type"]) for column in info]
        # athena repeats the header as the first data row and gives every value as a string
        rows = page["ResultSet"]["Rows"][1:]
        return [
            {
                name: _typed(cell.get("VarCharValue"), kind)
                for name, kind, cell in zip(names, types, row["Data"], strict=True)
            }
            for row in rows
        ]


def _typed(value: str | None, kind: str) -> Any:
    if value is None:
        return None
    if kind in {"tinyint", "smallint", "integer", "int", "bigint"}:
        return int(value)
    if kind in {"double", "float", "real", "decimal"}:
        return float(value)
    if kind == "boolean":
        return value == "true"
    return value


class DriverForm(BaseModel, frozen=True):
    driver_code: str = Field(description="Three letter driver code, for example VER or HAM.")
    n_events: int = Field(default=5, ge=1, le=40, description="How many recent races to return.")


class NoArguments(BaseModel, frozen=True):
    pass


class PaceProfile(BaseModel, frozen=True):
    event: str = Field(
        default="latest",
        description="'latest' or 'season:round'. Pace is measured, so a race has to have run.",
    )
    regime: str = Field(default="dry", description="'dry' or 'wet'.")


class TrackFit(BaseModel, frozen=True):
    circuit_id: str = Field(description="Circuit id as the lake spells it, for example monza.")
    constructor_id: str | None = Field(default=None, description="One team only, optional.")


class Weather(BaseModel, frozen=True):
    event: str = Field(default="next", description="'next' or 'season:round'.")


class Forecast(BaseModel, frozen=True):
    event: str = Field(default="next", description="'next' or 'season:round'.")
    market: str = Field(default="win", description=f"One of {', '.join(MARKETS)}.")


class MartQuery(BaseModel, frozen=True):
    sql: str = Field(description="A single read-only SELECT over the allowlisted gold marts.")


class DocQuery(BaseModel, frozen=True):
    query: str = Field(
        description="What to look for in the regulations, race documents and methodology notes."
    )
    top_k: int = Field(default=5, ge=1, le=10, description="How many passages to return.")
    source: str | None = Field(
        default=None,
        description=(
            "Restrict to one corpus source: fia_docs for the regulations, wikipedia for race and "
            "circuit write-ups, curated for this system's own methodology notes. Leave it out to "
            "search all of them, which is usually what you want."
        ),
    )


class SimRequest(BaseModel, frozen=True):
    event: str = Field(default="next", description="'next' or 'season:round'.")
    scenario: str = Field(default="dry", description="'dry', 'mixed' or 'wet'.")
    paths: int = Field(default=1000, ge=100, le=MAX_SIM_PATHS, description="Simulated races.")


@dataclass(frozen=True)
class Toolbox:
    store: ObjectStore
    marts: Marts | None = None
    docs: Retriever | None = None
    sim: Simulator | None = None

    def get_driver_form(self, request: DriverForm) -> ToolResult:
        view = self._view("driver_view", DriverView)
        code = request.driver_code.upper()
        driver = next((item for item in view.drivers if item.driver_code == code), None)
        if driver is None:
            known = ", ".join(sorted(item.driver_code for item in view.drivers))
            raise ToolError(f"no driver {code} in the published view. it holds {known}")
        recent = sorted(driver.pace, key=lambda item: item.race_date)[-request.n_events :]
        teammate = sorted(driver.teammate, key=lambda item: item.race_date)[-request.n_events :]
        return self._result(
            "get_driver_form",
            {
                "driver_code": driver.driver_code,
                "constructor_id": driver.constructor_id,
                "as_of": view.as_of.isoformat(),
                "half_life_events": view.half_life_events,
                "interval_level": INTERVAL_LEVEL,
                "form": _dump(driver.form),
                "form_component": driver.form_component,
                "quali_race": _dump(driver.quali_race),
                "wet": _dump(driver.wet),
                "pace": [item.model_dump(mode="json") for item in recent],
                "teammate": [item.model_dump(mode="json") for item in teammate],
            },
            "driver_view",
        )

    def get_pace_profile(self, request: PaceProfile) -> ToolResult:
        view = self._view("driver_view", DriverView)
        regime = request.regime.lower()
        fitted = sorted(
            {
                (sample.season, sample.round)
                for driver in view.drivers
                for sample in driver.pace
                if sample.regime == regime
            }
        )
        if not fitted:
            raise ToolError(f"the published view holds no {regime} pace fit at all")
        season, round_ = self._fitted_event(request.event, fitted, regime)
        rows = [
            {
                "driver_code": driver.driver_code,
                "constructor_id": driver.constructor_id,
                "percent_off_benchmark": sample.percent_off_benchmark,
                "circuit_id": sample.circuit_id,
            }
            for driver in view.drivers
            for sample in driver.pace
            if sample.season == season and sample.round == round_ and sample.regime == regime
        ]
        return self._result(
            "get_pace_profile",
            {
                "season": season,
                "round": round_,
                "regime": regime,
                "drivers": sorted(rows, key=lambda row: row["percent_off_benchmark"]),
            },
            "driver_view",
        )

    def get_track_fit(self, request: TrackFit) -> ToolResult:
        view = self._view("track_view", TrackView)
        if view.profile.circuit_id != request.circuit_id:
            raise ToolError(
                f"the published track view covers {view.profile.circuit_id}, not "
                f"{request.circuit_id}"
            )
        teams = [
            team
            for team in view.teams
            if request.constructor_id is None or team.constructor_id == request.constructor_id
        ]
        if not teams:
            raise ToolError(f"no team {request.constructor_id} at {request.circuit_id}")
        return self._result(
            "get_track_fit",
            {
                "circuit_id": view.profile.circuit_id,
                "interval_level": INTERVAL_LEVEL,
                "profile": view.profile.model_dump(mode="json"),
                "neighbours": [item.model_dump(mode="json") for item in view.neighbours],
                "teams": [
                    {
                        "constructor_id": team.constructor_id,
                        "regression": _dump(team.regression),
                        "similarity": _dump(team.similarity),
                        "estimators_disagree": team.disagree,
                    }
                    for team in teams
                ],
            },
            "track_view",
        )

    def get_weather(self, request: Weather) -> ToolResult:
        view = self._view("weekend_view", WeekendView)
        self._event_of(request.event, view.event.season, view.event.round)
        if view.weather is None:
            raise ToolError("no forecast covers this session window yet")
        return self._result(
            "get_weather",
            {
                "season": view.event.season,
                "round": view.event.round,
                "circuit_id": view.event.circuit_id,
                "scenarios": view.weather.model_dump(mode="json"),
            },
            "weekend_view",
        )

    def get_forecast(self, request: Forecast) -> ToolResult:
        view = self._view("forecast_view", ForecastView)
        self._event_of(request.event, view.event.season, view.event.round)
        market = request.market.lower()
        if market not in MARKETS:
            raise ToolError(f"{market} is not a market. try one of {', '.join(MARKETS)}")
        rows = [
            {
                "driver_code": driver.driver_code,
                "constructor_id": driver.constructor_id,
                "grid": driver.grid,
                market: getattr(driver, market),
            }
            for driver in view.drivers
        ]
        return self._result(
            "get_forecast",
            {
                "season": view.event.season,
                "round": view.event.round,
                "market": market,
                "paths": view.paths,
                "scenarios": [item.model_dump(mode="json") for item in view.scenarios],
                "weights_are_forecast": view.weights_are_forecast,
                "drivers": rows,
                "evidence": _dump(view.evidence),
            },
            "forecast_view",
        )

    def get_calibration(self) -> ToolResult:
        view = self._view("calibration_view", CalibrationView)
        return self._result(
            "get_calibration",
            {
                "holdout": view.holdout,
                "from_season": view.from_season,
                "interval_level": INTERVAL_LEVEL,
                "beats_baselines": view.beats_baselines,
                "separated_from": view.separated_from,
                "scored": [
                    {
                        "name": item.name,
                        "races": item.races,
                        "log_loss": item.log_loss.model_dump(mode="json"),
                        "brier": item.brier.model_dump(mode="json"),
                    }
                    for item in view.scored
                ],
                "paired": [item.model_dump(mode="json") for item in view.paired],
            },
            "calibration_view",
        )

    def query_marts(self, request: MartQuery) -> ToolResult:
        if self.marts is None:
            raise ToolError("no mart backend is configured for this session")
        try:
            checked = guard(request.sql)
        except RejectedError as exc:
            raise ToolError(f"the query was rejected: {exc.reason}") from exc
        rows = self.marts.rows(checked)
        return ToolResult(
            tool="query_marts",
            ok=True,
            payload={"sql": checked, "row_count": len(rows), "rows": _jsonable(rows)},
            citations=[f"athena:{checked}"],
        )

    def retrieve_docs(self, request: DocQuery) -> ToolResult:
        if self.docs is None:
            raise ToolError("no knowledge base is configured for this session")
        # a filter nothing matches returns an empty list, which reads to the model as a corpus
        # with nothing in it. it has to fail as the mistake it is
        if request.source is not None and request.source not in CORPUS_SOURCES:
            raise ToolError(
                f"{request.source!r} is not a corpus source. they are "
                f"{', '.join(CORPUS_SOURCES)}, or leave it out to search all of them"
            )
        passages = self.docs.retrieve(request.query, request.top_k, request.source)
        if not passages:
            raise ToolError(f"nothing in the corpus matches {request.query!r}")
        return ToolResult(
            tool="retrieve_docs",
            ok=True,
            payload={"query": request.query, "passages": passages},
            citations=[str(item.get("uri", "")) for item in passages if item.get("uri")],
        )

    def run_race_sim(self, request: SimRequest) -> ToolResult:
        if self.sim is None:
            raise ToolError("no simulator is configured for this session")
        if request.scenario not in {"dry", "mixed", "wet"}:
            raise ToolError(f"{request.scenario} is not a scenario")
        payload = self.sim.simulate(request.event, request.scenario, request.paths)
        return ToolResult(
            tool="run_race_sim",
            ok=True,
            payload=payload,
            citations=[f"sim:{request.event}:{request.scenario}:{request.paths}"],
        )

    def _view(self, name: str, model: type[View]) -> View:
        key = view_key(name)
        try:
            body = self.store.get(key)
        except Exception as exc:
            raise ToolError(f"{key} has not been emitted yet") from exc
        return model.model_validate_json(body.decode())

    def _fitted_event(
        self, event: str, fitted: list[tuple[int, int]], regime: str
    ) -> tuple[int, int]:
        if event in {"latest", ""}:
            return fitted[-1]
        if event == "next":
            raise ToolError(
                "the upcoming race has not run, so it has no measured pace. "
                "ask for 'latest' or a season:round that has"
            )
        parsed = _parsed(event)
        if parsed not in fitted:
            span = f"{fitted[0][0]}:{fitted[0][1]} to {fitted[-1][0]}:{fitted[-1][1]}"
            raise ToolError(f"no {regime} pace fit for {event}. the view covers {span}")
        return parsed

    def _event_of(self, event: str, season: int, round_: int) -> tuple[int, int]:
        if event in {"next", "", f"{season}:{round_}"}:
            return season, round_
        raise ToolError(
            f"the published views cover {season}:{round_}, not {event}. "
            "history lives in the marts, ask query_marts"
        )

    def _result(self, tool: str, payload: dict[str, Any], view: str) -> ToolResult:
        return ToolResult(tool=tool, ok=True, payload=payload, citations=[view_key(view)])


def _parsed(event: str) -> tuple[int, int]:
    try:
        season, round_ = (int(part) for part in event.split(":", 1))
    except ValueError as exc:
        raise ToolError(f"{event!r} is not 'latest' or 'season:round'") from exc
    return season, round_


def _dump(value: BaseModel | None) -> dict[str, Any] | None:
    return None if value is None else value.model_dump(mode="json")


def _jsonable(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {key: value.isoformat() if isinstance(value, date) else value for key, value in row.items()}
        for row in rows
    ]


@dataclass
class RaceSim:
    """Runs the same simulation the forecast view was built from, one scenario at a time."""

    store: ObjectStore
    seed: int = 11
    _panel: Any = None

    def simulate(self, event: str, scenario: str, paths: int) -> dict[str, Any]:
        import numpy as np

        from pitadvisor.features import assemble
        from pitadvisor.model import backtest

        if self._panel is None:
            self._panel = backtest.panel(self.store)
        context = (
            assemble.next_event(self.store)
            if event in {"next", ""}
            else assemble.event_at(self.store, *_parsed(event))
        )
        predicted = backtest.forecast(
            self._panel,
            context,
            context.race_date,
            np.random.default_rng(self.seed),
            paths=min(paths, MAX_SIM_PATHS),
            weights={scenario: 1.0},
        )
        outcome = predicted.outcome
        return {
            "season": context.season,
            "round": context.round,
            "circuit_id": context.circuit_id,
            "scenario": scenario,
            "paths": predicted.paths,
            "laps": predicted.laps,
            "drivers": [
                {
                    "driver_code": code,
                    "win": outcome.win[index],
                    "podium": outcome.podium[index],
                    "points": outcome.points[index],
                    "expected_position": outcome.expected_position[index],
                }
                for index, code in enumerate(outcome.driver_code)
            ],
        }


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: type[BaseModel]
    call: Callable[[Toolbox, BaseModel], ToolResult]


def _bind(
    method: Callable[[Toolbox, Any], ToolResult],
) -> Callable[[Toolbox, BaseModel], ToolResult]:
    return cast(Callable[[Toolbox, BaseModel], ToolResult], method)


TOOLS: Final[tuple[Tool, ...]] = (
    Tool(
        "get_driver_form",
        "Teammate-normalised, time-decayed form for one driver: the rating and its interval, "
        "the qualifying-to-race trend, the wet delta, the decay half life, and the recent races "
        "and teammate deltas behind it.",
        DriverForm,
        _bind(Toolbox.get_driver_form),
    ),
    Tool(
        "get_pace_profile",
        "Measured clean-air race pace for every driver at a race that has already run, as a "
        "percentage off the benchmark. Defaults to the most recent race with a fit.",
        PaceProfile,
        _bind(Toolbox.get_pace_profile),
    ),
    Tool(
        "get_track_fit",
        "How each team's pace fits one circuit, by regression and by similar-circuit history, "
        "with the circuit's own profile and the circuits it most resembles.",
        TrackFit,
        _bind(Toolbox.get_track_fit),
    ),
    Tool(
        "get_weather",
        "What the weather feature makes of the event window: dry, mixed and wet weights, "
        "expected rainfall, and whether it is a real forecast or an archived observation. For "
        "the weights the simulation actually blended over, use get_forecast.",
        Weather,
        _bind(Toolbox.get_weather),
    ),
    Tool(
        "get_forecast",
        "Simulated finishing probabilities for the upcoming event: win, podium, points, finish "
        "and expected position, plus the path count, the scenario weights and the backtest "
        "evidence the forecast was judged by.",
        Forecast,
        _bind(Toolbox.get_forecast),
    ),
    Tool(
        "get_calibration",
        "How the forecast scored against every baseline on the time-forward holdout.",
        NoArguments,
        lambda box, _request: box.get_calibration(),
    ),
    Tool(
        "query_marts",
        "Run one read-only SELECT over the gold marts for anything the typed tools do not cover.",
        MartQuery,
        _bind(Toolbox.query_marts),
    ),
    Tool(
        "retrieve_docs",
        "Search the regulations, race write-ups and this system's notes on how its own metrics "
        "are computed. Returns passages to cite.",
        DocQuery,
        _bind(Toolbox.retrieve_docs),
    ),
    Tool(
        "run_race_sim",
        "Re-run the race simulation under one weather scenario, for counterfactual questions.",
        SimRequest,
        _bind(Toolbox.run_race_sim),
    ),
)

BY_NAME: Final[dict[str, Tool]] = {tool.name: tool for tool in TOOLS}


def specs() -> list[dict[str, Any]]:
    return [
        {
            "toolSpec": {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": {"json": _schema_of(tool.schema)},
            }
        }
        for tool in TOOLS
    ]


def _schema_of(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    schema.pop("title", None)
    for field in schema.get("properties", {}).values():
        field.pop("title", None)
    return schema


def invoke(box: Toolbox, name: str, arguments: dict[str, Any]) -> ToolResult:
    tool = BY_NAME.get(name)
    if tool is None:
        return ToolResult(tool=name, ok=False, detail=f"there is no tool called {name}")
    try:
        request = tool.schema.model_validate(arguments)
    except ValueError as exc:
        return ToolResult(tool=name, ok=False, detail=f"bad arguments: {exc}")
    try:
        return tool.call(box, request)
    except ToolError as exc:
        return ToolResult(tool=name, ok=False, detail=str(exc))


def toolbox(settings: Settings, store: ObjectStore, local: bool = False) -> Toolbox:
    from pitadvisor.agent import kb

    if local:
        return Toolbox(
            store=store,
            marts=DuckDBMarts(),
            docs=kb.LocalCorpus(store),
            sim=RaceSim(store),
        )
    session = cast(Any, boto_session(settings))
    corpus = (
        kb.KnowledgeBase(
            session.client("bedrock-agent-runtime", region_name=settings.aws_region),
            settings.knowledge_base_id,
        )
        if settings.knowledge_base_id
        else kb.LocalCorpus(store)
    )
    return Toolbox(
        store=store,
        marts=AthenaMarts(
            session.client("athena", region_name=settings.aws_region),
            settings.glue_database,
            settings.athena_workgroup,
        ),
        docs=corpus,
        sim=RaceSim(store),
    )


def numbers_in(result: ToolResult) -> set[str]:
    """Every figure a tool returned, as the strings an answer would have to quote."""
    found: set[str] = set()
    _walk(result.payload, found)
    return found


def _walk(node: Any, found: set[str]) -> None:
    if isinstance(node, bool):
        return
    if isinstance(node, int | float):
        found.update(renderings(float(node)))
        return
    if isinstance(node, str):
        for token in IN_TEXT.findall(RANGE.sub(" ", node)):
            try:
                found.update(renderings(float(token.replace(",", ""))))
            except ValueError:
                continue
        return
    if isinstance(node, dict):
        for value in cast(dict[str, Any], node).values():
            _walk(value, found)
        return
    if isinstance(node, list):
        for value in cast(list[Any], node):
            _walk(value, found)


def renderings(value: float) -> set[str]:
    out: set[str] = set()
    # both signs: "0.26 percent off the benchmark on the quick side" has quoted -0.26 and put
    # the direction in words, which is not the same thing as inventing a number
    for number in {value, abs(value)}:
        out |= {repr(number), f"{number:g}"}
        if number.is_integer():
            out.add(str(int(number)))
        for places in range(0, 5):
            for scaled in (number, number * 100):
                out.add(f"{scaled:.{places}f}")
                out.add(_dropped(scaled, places))
    return out


# a model that writes 0.245 for 0.24550 quoted the tool and dropped the tail, and rounding on
# its own calls that an invented figure and withholds a correct answer
def _dropped(value: float, places: int) -> str:
    try:
        return str(Decimal(value).quantize(Decimal(1).scaleb(-places), rounding=ROUND_DOWN))
    except (InvalidOperation, ValueError, OverflowError):
        return f"{value:.{places}f}"
