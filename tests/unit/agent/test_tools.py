import duckdb
import pytest

from pitadvisor.agent import tools
from pitadvisor.agent.tools import (
    AthenaMarts,
    DriverForm,
    DuckDBMarts,
    Toolbox,
    ToolError,
    invoke,
)


@pytest.fixture
def box(views):
    return Toolbox(store=views)


@pytest.fixture
def marts(tmp_path):
    path = tmp_path / "pitadvisor.duckdb"
    connection = duckdb.connect(str(path))
    connection.sql(
        "create table gold_race_results as select * from (values "
        "(2024, 5, 'AAA', 1), (2024, 5, 'BBA', 2), (2023, 5, 'AAA', 4)) "
        "as t(season, round, driver_code, position)"
    )
    connection.close()
    return DuckDBMarts(path)


def test_driver_form_comes_back_with_its_interval_and_its_sample_count(box):
    result = invoke(box, "get_driver_form", {"driver_code": "aaa"})
    assert result.ok
    assert result.payload["driver_code"] == "AAA"
    assert set(result.payload["form"]) >= {"value", "low", "high", "samples"}


def test_driver_form_cites_the_view_it_read(box):
    assert invoke(box, "get_driver_form", {"driver_code": "AAA"}).citations == [
        "views/driver_view.json"
    ]


def test_a_driver_who_is_not_in_the_view_is_a_failure_not_an_empty_answer(box):
    result = invoke(box, "get_driver_form", {"driver_code": "ZZZ"})
    assert not result.ok
    assert "no driver ZZZ" in result.detail


def test_the_recent_window_is_honoured(box):
    few = invoke(box, "get_driver_form", {"driver_code": "AAA", "n_events": 2})
    assert len(few.payload["pace"]) <= 2


def test_pace_profile_is_ordered_from_the_benchmark_outwards(box):
    result = invoke(box, "get_pace_profile", {})
    assert result.ok
    values = [row["percent_off_benchmark"] for row in result.payload["drivers"]]
    assert values == sorted(values)


def test_a_regime_with_no_fit_says_so_rather_than_returning_nothing(box):
    result = invoke(box, "get_pace_profile", {"regime": "wet"})
    assert not result.ok
    assert "no wet pace fit at all" in result.detail


def test_the_upcoming_race_has_no_measured_pace_and_the_tool_says_so(box):
    result = invoke(box, "get_pace_profile", {"event": "next"})
    assert not result.ok
    assert "has not run" in result.detail


def test_an_event_outside_the_published_window_reports_the_window(box):
    result = invoke(box, "get_pace_profile", {"event": "2021:1"})
    assert not result.ok
    assert "the view covers" in result.detail


def test_asking_for_an_event_the_views_do_not_cover_points_at_the_marts(box):
    result = invoke(box, "get_weather", {"event": "2021:1"})
    assert not result.ok
    assert "query_marts" in result.detail


def test_track_fit_carries_both_estimators_and_whether_they_disagree(box, views):
    circuit = tools.TrackView.model_validate_json(
        views.get("views/track_view.json").decode()
    ).profile.circuit_id
    result = invoke(box, "get_track_fit", {"circuit_id": circuit})
    assert result.ok
    assert {"regression", "similarity", "estimators_disagree"} <= set(result.payload["teams"][0])


def test_the_wrong_circuit_is_refused(box):
    result = invoke(box, "get_track_fit", {"circuit_id": "interlagos"})
    assert not result.ok
    assert "not interlagos" in result.detail


def test_weather_comes_back_as_scenario_weights(box):
    result = invoke(box, "get_weather", {})
    assert result.ok
    assert set(result.payload["scenarios"]) >= {"dry", "mixed", "wet"}


def test_the_forecast_carries_the_evidence_that_judged_it(box):
    result = invoke(box, "get_forecast", {"market": "win"})
    assert result.ok
    assert result.payload["evidence"]["holdout"] == 3
    assert all("win" in row for row in result.payload["drivers"])


def test_a_market_that_does_not_exist_is_refused(box):
    result = invoke(box, "get_forecast", {"market": "fastest_lap"})
    assert not result.ok
    assert "is not a market" in result.detail


def test_calibration_reports_every_scored_model(box):
    result = invoke(box, "get_calibration", {})
    assert result.ok
    assert {item["name"] for item in result.payload["scored"]} >= {"simulation", "grid"}


def test_query_marts_runs_a_guarded_select(box, marts):
    result = invoke(
        Toolbox(store=box.store, marts=marts),
        "query_marts",
        {"sql": "select driver_code from gold_race_results where season = 2024"},
    )
    assert result.ok
    assert result.payload["row_count"] == 2
    assert "LIMIT" in result.payload["sql"]


def test_query_marts_refuses_what_the_guard_refuses(box, marts):
    result = invoke(
        Toolbox(store=box.store, marts=marts),
        "query_marts",
        {"sql": "drop table gold_race_results"},
    )
    assert not result.ok
    assert "rejected" in result.detail


def test_query_marts_without_a_backend_says_so(box):
    assert "no mart backend" in invoke(box, "query_marts", {"sql": "select 1"}).detail


def test_a_missing_local_mart_file_is_a_failure_with_the_fix_in_it(tmp_path):
    with pytest.raises(ToolError) as caught:
        DuckDBMarts(tmp_path / "nothing.duckdb").rows("select 1")
    assert "dbt build" in str(caught.value)


def test_retrieve_docs_needs_a_knowledge_base(box):
    assert "no knowledge base" in invoke(box, "retrieve_docs", {"query": "parc ferme"}).detail


def test_retrieve_docs_returns_passages_and_their_citations(box):
    class Corpus:
        def retrieve(self, query, top_k, source):
            return [{"text": "parc ferme opens", "uri": "s3://docs/sporting.pdf", "score": 0.4}]

    result = invoke(
        Toolbox(store=box.store, docs=Corpus()), "retrieve_docs", {"query": "parc ferme"}
    )
    assert result.ok
    assert result.citations == ["s3://docs/sporting.pdf"]


def test_a_corpus_with_no_match_is_a_failure(box):
    class Empty:
        def retrieve(self, query, top_k, source):
            return []

    result = invoke(Toolbox(store=box.store, docs=Empty()), "retrieve_docs", {"query": "nothing"})
    assert not result.ok


def test_the_simulator_is_bounded_by_the_schema(box):
    class Sim:
        def simulate(self, event, scenario, paths):
            return {"paths": paths}

    result = invoke(
        Toolbox(store=box.store, sim=Sim()),
        "run_race_sim",
        {"scenario": "wet", "paths": tools.MAX_SIM_PATHS + 5000},
    )
    assert not result.ok
    assert "bad arguments" in result.detail


def test_an_unknown_tool_is_reported_not_raised(box):
    assert "no tool called" in invoke(box, "get_lap_times", {}).detail


def test_every_tool_publishes_a_schema_bedrock_can_read():
    names = {spec["toolSpec"]["name"] for spec in tools.specs()}
    assert names == set(tools.BY_NAME)
    for spec in tools.specs():
        assert spec["toolSpec"]["inputSchema"]["json"]["type"] == "object"
        assert spec["toolSpec"]["description"]


def test_the_figures_a_tool_returned_are_recoverable_for_the_grounding_check(box):
    result = invoke(box, "get_forecast", {"market": "win"})
    win = result.payload["drivers"][0]["win"]
    assert f"{win:.2f}" in tools.numbers_in(result)
    assert f"{win * 100:.1f}" in tools.numbers_in(result)


def test_arguments_that_do_not_fit_the_schema_are_refused(box):
    assert "bad arguments" in invoke(box, "get_driver_form", {"n_events": 0}).detail


def test_the_driver_form_schema_documents_its_fields():
    schema = tools.BY_NAME["get_driver_form"].schema.model_json_schema()
    assert schema["properties"]["driver_code"]["description"]
    assert DriverForm(driver_code="ver").n_events == 5


class FakeAthena:
    def __init__(self, state="SUCCEEDED"):
        self.state = state
        self.queries = []

    def start_query_execution(self, **kwargs):
        self.queries.append(kwargs)
        return {"QueryExecutionId": "q-1"}

    def get_query_execution(self, **_):
        return {"QueryExecution": {"Status": {"State": self.state, "StateChangeReason": "boom"}}}

    def get_query_results(self, **_):
        return {
            "ResultSet": {
                "ResultSetMetadata": {
                    "ColumnInfo": [
                        {"Name": "driver_code", "Type": "varchar"},
                        {"Name": "wins", "Type": "bigint"},
                        {"Name": "share", "Type": "double"},
                    ]
                },
                "Rows": [
                    {"Data": [{"VarCharValue": "driver_code"}, {"VarCharValue": "wins"}, {}]},
                    {
                        "Data": [
                            {"VarCharValue": "VER"},
                            {"VarCharValue": "9"},
                            {"VarCharValue": "0.45"},
                        ]
                    },
                ],
            }
        }


def test_athena_returns_typed_rows_without_the_header_row():
    marts = AthenaMarts(FakeAthena(), "pitadvisor_dev", "pitadvisor", sleep=lambda _: None)
    assert marts.rows("select 1") == [{"driver_code": "VER", "wins": 9, "share": 0.45}]


def test_athena_names_the_workgroup_and_the_database():
    client = FakeAthena()
    AthenaMarts(client, "pitadvisor_dev", "pitadvisor", sleep=lambda _: None).rows("select 1")
    assert client.queries[0]["WorkGroup"] == "pitadvisor"
    assert client.queries[0]["QueryExecutionContext"]["Database"] == "pitadvisor_dev"


def test_a_failed_athena_query_says_why():
    marts = AthenaMarts(FakeAthena("FAILED"), "db", "wg", sleep=lambda _: None)
    with pytest.raises(ToolError) as caught:
        marts.rows("select 1")
    assert "boom" in str(caught.value)
