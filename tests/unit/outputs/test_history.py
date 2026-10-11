from datetime import timedelta

from pitadvisor.features.assemble import event_at
from pitadvisor.outputs import history, published
from tests.unit.outputs.test_published import issued


def test_every_kept_forecast_comes_back_oldest_first(seeded):
    built = seeded()
    _, early, lights = issued(built, hours_before=30, run_id="run-early")
    _, late, _ = issued(built, hours_before=2, run_id="run-late")
    published.archive(built.store, late, "race", lights)
    published.archive(built.store, early, "race", lights)
    view = history.history_view(built.store, event_at(built.store, 2024, 6), "run-x")
    assert [item.run_id for item in view.snapshots] == ["run-early", "run-late"]
    assert view.snapshots[0].stage == "after_qualifying"
    assert {item.driver_code for item in view.snapshots[0].drivers} == {
        item.driver_code for item in early.drivers
    }


def test_a_rerun_with_the_same_numbers_is_marked_a_repeat(seeded):
    built = seeded()
    _, first, lights = issued(built, hours_before=30, run_id="run-a")
    again = first.model_copy(
        update={"run_id": "run-b", "generated_at": first.generated_at + timedelta(hours=1)}
    )
    published.archive(built.store, first, "race", lights)
    published.archive(built.store, again, "race", lights)
    view = history.history_view(built.store, event_at(built.store, 2024, 6), "run-x")
    assert [item.repeat for item in view.snapshots] == [False, True]


def test_another_event_is_not_mixed_in(seeded):
    built = seeded()
    _, view, lights = issued(built)
    published.archive(built.store, view, "race", lights)
    other = history.history_view(built.store, event_at(built.store, 2024, 5), "run-x")
    assert other.snapshots == []
