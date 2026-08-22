import pytest

from pitadvisor.quality import catalog
from pitadvisor.quality.contracts import TABLES, BronzeRow


class FakeGlue:
    def __init__(self, tables=None):
        self.tables = tables or {}
        self.created = []
        self.updated = []

    def get_table(self, DatabaseName, Name):
        if Name not in self.tables:
            raise KeyError(Name)
        return {"Table": self.tables[Name]}

    def create_table(self, DatabaseName, TableInput):
        self.created.append(TableInput["Name"])
        self.tables[TableInput["Name"]] = TableInput

    def update_table(self, DatabaseName, TableInput):
        self.updated.append(TableInput["Name"])
        self.tables[TableInput["Name"]] = TableInput


def test_optional_columns_keep_their_inner_type():
    assert catalog.hive_type(int | None) == "bigint"
    assert catalog.hive_type(str) == "string"
    assert catalog.hive_type(list[int]) is None


def test_a_session_kind_is_a_string_in_glue():
    definition = catalog.table_input("session_laps", TABLES["session_laps"], "bucket")
    session = next(key for key in definition["PartitionKeys"] if key["Name"] == "session")
    assert session["Type"] == "string"


def test_partition_columns_are_not_also_columns():
    definition = catalog.table_input("results", TABLES["results"], "bucket")
    names = {column["Name"] for column in definition["StorageDescriptor"]["Columns"]}
    assert "season" not in names
    assert "round" not in names
    assert "driver_id" in names


def test_an_unmappable_column_is_loud():
    class Odd(BronzeRow):
        weird: list[int]

    with pytest.raises(catalog.UnmappedTypeError):
        catalog.columns("odd", Odd)


def test_projection_pads_the_round_and_not_the_season():
    properties = catalog.projection("results", "s3://bucket/bronze/table=results/")
    assert properties["projection.round.digits"] == "2"
    assert "projection.season.digits" not in properties
    assert properties["storage.location.template"].endswith("season=${season}/round=${round}/")


def test_session_tables_project_the_session_too():
    properties = catalog.projection("session_laps", "s3://bucket/bronze/table=session_laps/")
    assert "race" in properties["projection.session.values"]
    assert properties["storage.location.template"].endswith("session=${session}/")


def test_sync_creates_every_bronze_table_once():
    glue = FakeGlue()
    actions = catalog.sync(glue, "db", "bucket")
    assert {action.action for action in actions} == {"create"}
    assert set(glue.created) == set(TABLES)


def test_a_second_sync_changes_nothing():
    glue = FakeGlue()
    catalog.sync(glue, "db", "bucket")
    actions = catalog.sync(glue, "db", "bucket")
    assert {action.action for action in actions} == {"unchanged"}
    assert not glue.updated


def test_a_moved_location_is_drift():
    glue = FakeGlue()
    catalog.sync(glue, "db", "bucket")
    glue.tables["results"]["StorageDescriptor"]["Location"] = "s3://somewhere/else/"
    actions = {action.table: action for action in catalog.sync(glue, "db", "bucket")}
    assert actions["results"].action == "update"
    assert "location" in actions["results"].detail
    assert glue.updated == ["results"]


def test_check_mode_writes_nothing():
    glue = FakeGlue()
    catalog.sync(glue, "db", "bucket", apply=False)
    assert not glue.created
