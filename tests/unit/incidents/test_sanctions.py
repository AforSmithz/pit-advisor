from pitadvisor.incidents.sanctions import Kind, sanctions


def kinds(outcome):
    return [sanction.kind for sanction in sanctions(outcome)]


def test_the_commonest_outcome_is_no_penalty_at_all():
    assert kinds("No further action.") == [Kind.NONE]
    assert kinds("No penalty is applied.") == [Kind.NONE]


def test_one_decision_carries_several_sanctions_in_the_order_written():
    found = sanctions(
        "10 second time penalty. 2 penalty points (total of 4 for the 12 month period)."
    )
    assert [sanction.kind for sanction in found] == [Kind.TIME_PENALTY, Kind.PENALTY_POINTS]
    assert found[0].seconds == 10
    assert (found[1].points, found[1].points_total) == (2, 4)


def test_a_grid_drop_keeps_its_positions():
    assert sanctions("Drop of 5 grid positions.")[0].positions == 5
    assert sanctions("10 grid position penalty.")[0].positions == 10


def test_a_fine_is_read_with_its_currency_written_either_way():
    symbol = sanctions("The competitor (Williams Racing) is fined €100.")[0]
    assert (symbol.kind, symbol.amount, symbol.currency) == (Kind.FINE, 100, "EUR")
    spelled = sanctions("Fine of 5,000 euros.")[0]
    assert (spelled.amount, spelled.currency) == (5000, "EUR")


def test_a_penalty_written_in_words_is_read_as_a_number():
    found = sanctions("Ten Second Stop and Go Penalty.")
    assert (found[0].kind, found[0].seconds) == (Kind.STOP_AND_GO, 10)
    assert sanctions("10 Seconds Stop and Go.")[0].seconds == 10


def test_a_stop_and_go_is_not_also_read_as_a_time_penalty():
    assert kinds("5 second stop and go penalty.") == [Kind.STOP_AND_GO]


def test_the_pit_lane_is_not_a_grid_drop():
    assert kinds("Required to start the Race from the pit lane.") == [Kind.PIT_LANE_START]
    assert kinds("Required to start the race from the rear of the starting grid.") == [
        Kind.BACK_OF_GRID
    ]


def test_a_suspended_fine_is_both_facts_not_one():
    assert kinds("The competitor is fined €25,000, suspended for 12 months.") == [
        Kind.FINE,
        Kind.SUSPENDED,
    ]


def test_every_sanction_quotes_the_outcome_it_came_from():
    outcome = "Reprimand (Driving). This is the driver\u2019s 1st reprimand of the season."
    for sanction in sanctions(outcome):
        assert sanction.text in outcome


def test_an_outcome_that_imposes_nothing_yields_nothing():
    assert sanctions("The Protest is rejected as it is not founded.") == []
    assert sanctions(None) == []
    assert sanctions("") == []
