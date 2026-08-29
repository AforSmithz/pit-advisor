import pytest

from pitadvisor.agent import sql_guard
from pitadvisor.agent.sql_guard import RejectedError, guard


def rejected(sql: str) -> str:
    with pytest.raises(RejectedError) as caught:
        guard(sql)
    return caught.value.reason


def test_a_plain_select_over_a_mart_passes():
    assert guard("select driver_id from gold_race_results where season = 2024").startswith("SELECT")


def test_a_second_statement_after_a_semicolon_is_rejected():
    assert "2 statements" in rejected(
        "select 1 from gold_race_results; drop table gold_race_results"
    )


def test_a_statement_commented_out_of_sight_is_rejected():
    assert "2 statements" in rejected(
        "select season from gold_race_results -- harmless\n; delete from gold_race_results"
    )


def test_comments_do_not_survive_into_the_query_that_runs():
    assert "--" not in guard("select season from gold_race_results -- pick the season")


@pytest.mark.parametrize(
    "sql",
    [
        "insert into gold_race_results values (1)",
        "update gold_race_results set points = 99",
        "delete from gold_race_results",
        "create table t as select * from gold_race_results",
        "drop table gold_race_results",
        "alter table gold_race_results add column x int",
        "grant select on gold_race_results to public",
        "unload (select * from gold_race_results) to 's3://somewhere/'",
    ],
)
def test_every_write_is_rejected(sql):
    assert "select" in rejected(sql)


def test_a_union_reaching_a_table_that_is_not_a_mart_is_rejected():
    assert "select" in rejected(
        "select season from gold_race_results union all select 1 from information_schema.tables"
    )


def test_a_union_is_rejected_even_between_two_marts():
    assert "select" in rejected(
        "select season from gold_race_results union select season from gold_qualifying_gaps"
    )


def test_a_cte_over_a_mart_passes():
    sql = guard(
        "with wins as (select driver_id from gold_race_results where is_winner)"
        " select driver_id from wins"
    )
    assert "wins" in sql


def test_a_cte_hiding_another_table_is_rejected():
    assert "information_schema" in rejected(
        "with leak as (select * from information_schema.columns) select * from leak"
    )


def test_a_cte_named_after_a_mart_does_not_launder_its_body():
    assert "sys" in rejected(
        "with gold_race_results as (select * from sys.tables) select * from gold_race_results"
    )


def test_a_nested_select_on_a_table_that_is_not_a_mart_is_rejected():
    assert "audit" in rejected(
        "select season from gold_race_results where season in (select season from audit)"
    )


def test_a_join_across_two_marts_passes():
    sql = guard(
        "select r.driver_id from gold_race_results r"
        " join gold_qualifying_gaps q on q.event_id = r.event_id"
    )
    assert "gold_qualifying_gaps" in sql


def test_a_join_that_smuggles_one_forbidden_table_is_rejected():
    assert "secrets" in rejected(
        "select r.driver_id from gold_race_results r join secrets s on s.id = r.driver_id"
    )


def test_a_silver_table_is_not_a_mart():
    assert "silver_results" in rejected("select * from silver_results")


def test_naming_a_catalog_or_schema_is_rejected():
    assert "catalog or schema" in rejected(
        "select * from awsdatacatalog.pitadvisor_dev.gold_race_results"
    )


def test_an_uncapped_query_comes_back_with_the_cap():
    assert guard("select season from gold_race_results").endswith(f"LIMIT {sql_guard.MAX_LIMIT}")


def test_a_smaller_limit_is_left_alone():
    assert guard("select season from gold_race_results limit 5").endswith("LIMIT 5")


def test_a_bigger_limit_is_pulled_down_to_the_cap():
    assert guard("select season from gold_race_results limit 100000").endswith(
        f"LIMIT {sql_guard.MAX_LIMIT}"
    )


@pytest.mark.parametrize("clause", ["limit 1 + 1", "limit (select 5)", "limit cast(1 as bigint)"])
def test_a_limit_that_is_not_a_plain_number_is_rejected(clause):
    assert "plain integer" in rejected(f"select season from gold_race_results {clause}")


def test_syntax_that_will_not_parse_is_rejected_rather_than_passed_through():
    assert "will not parse" in rejected("select from where gold_race_results )(")


def test_an_empty_query_is_rejected():
    assert "empty" in rejected("   ")


def test_the_allowlist_can_be_narrowed_by_the_caller():
    with pytest.raises(RejectedError):
        guard("select * from gold_qualifying_gaps", allowed=("gold_race_results",))
