import json
from datetime import UTC, date, datetime

import pytest

from pitadvisor.outputs.cost import (
    CostReport,
    ServiceCost,
    cost_view,
    fetch,
    history,
    period,
    summarise,
)


def money(amount):
    return {"UnblendedCost": {"Amount": str(amount), "Unit": "USD"}}


def groups(pairs):
    return [{"Keys": [key], "Metrics": money(amount)} for key, amount in pairs]


class FakeCostExplorer:
    """Answers by what the request groups and filters on, the way the real API slices one bill."""

    def __init__(self, months, page_size=None):
        self.months = months
        self.page_size = page_size
        self.calls = []

    def _groups(self, month, params):
        key = params["GroupBy"][0]["Key"]
        text = json.dumps(params.get("Filter", {}))
        if key == "RECORD_TYPE":
            return [("Usage", month["usage"]), ("Credit", -month["usage"]), ("Tax", 0)]
        rows = month["tagged"] if "pit-advisor" in text else month["services"]
        return list(rows.items())

    def get_cost_and_usage(self, **params):
        self.calls.append(params)
        start = params["TimePeriod"]["Start"][:7]
        results = [
            {
                "TimePeriod": {"Start": f"{name}-01", "End": "-"},
                "Groups": groups(self._groups(month, params)),
                "Estimated": month.get("estimated", False),
            }
            for name, month in sorted(self.months.items())
            if name >= start
        ]
        if self.page_size is None:
            return {"ResultsByTime": results}
        offset = int(params.get("NextPageToken", 0))
        page = results[offset : offset + self.page_size]
        more = offset + self.page_size < len(results)
        return {
            "ResultsByTime": page,
            **({"NextPageToken": str(offset + self.page_size)} if more else {}),
        }


OCTOBER = {
    "usage": 0.4640,
    "estimated": True,
    "services": {
        "Cohere Embed 3 Model - English (Amazon Bedrock Edition)": 0.2415,
        "Amazon Simple Storage Service": 0.1976,
        "Amazon EC2 Container Registry (ECR)": 0.0249,
        "AWS CloudFormation": 0.0,
    },
    "tagged": {
        "Amazon Simple Storage Service": 0.1976,
        "Amazon EC2 Container Registry (ECR)": 0.0249,
    },
}
SEPTEMBER = {"usage": 0.827, "services": {"Amazon Simple Storage Service": 0.827}, "tagged": {}}


def test_the_current_month_ends_tomorrow_so_today_is_counted():
    assert period("current", date(2026, 10, 7)) == (date(2026, 10, 1), date(2026, 10, 8))


def test_a_closed_month_ends_on_the_first_of_the_next():
    assert period("2026-09", date(2026, 10, 7)) == (date(2026, 9, 1), date(2026, 10, 1))


def test_december_rolls_into_the_next_year():
    assert period("2025-12", date(2026, 10, 7)) == (date(2025, 12, 1), date(2026, 1, 1))


@pytest.mark.parametrize("month", ["2026-11", "october", "2026-13"])
def test_a_future_or_malformed_month_is_refused(month):
    with pytest.raises(ValueError, match=r"month|started"):
        period(month, date(2026, 10, 7))


def test_the_report_counts_usage_before_credits():
    report = fetch(FakeCostExplorer({"2026-10": OCTOBER}), date(2026, 10, 1), date(2026, 10, 8), 20)
    # credits zero the bill on this account, so they cannot be what the ceiling measures
    assert report.usage_usd == pytest.approx(0.464)
    assert report.credits_usd == pytest.approx(-0.464)
    assert report.under_ceiling
    assert report.estimated


def test_bedrock_shows_up_as_untagged_spend():
    report = fetch(FakeCostExplorer({"2026-10": OCTOBER}), date(2026, 10, 1), date(2026, 10, 8), 20)
    assert report.tagged_usd == pytest.approx(0.2225)
    assert report.untagged_usd == pytest.approx(0.2415)
    bedrock = next(row for row in report.services if row.service.startswith("Cohere"))
    assert bedrock.tagged_usd == 0


def test_services_are_largest_first_and_zero_lines_are_dropped():
    report = fetch(FakeCostExplorer({"2026-10": OCTOBER}), date(2026, 10, 1), date(2026, 10, 8), 20)
    assert [row.usage_usd for row in report.services] == sorted(
        (row.usage_usd for row in report.services), reverse=True
    )
    assert "AWS CloudFormation" not in {row.service for row in report.services}


def test_only_usage_lines_are_summed_by_service():
    client = FakeCostExplorer({"2026-10": OCTOBER})
    fetch(client, date(2026, 10, 1), date(2026, 10, 8), 20)
    by_service = [call for call in client.calls if call["GroupBy"][0]["Key"] == "SERVICE"]
    assert len(by_service) == 2
    for call in by_service:
        assert "RECORD_TYPE" in json.dumps(call["Filter"])
    tagged = next(call for call in by_service if "And" in call["Filter"])
    assert {"Key": "project", "Values": ["pit-advisor"]} in [
        clause.get("Tags") for clause in tagged["Filter"]["And"]
    ]


def test_a_bill_over_the_ceiling_fails():
    report = fetch(
        FakeCostExplorer({"2026-10": OCTOBER}), date(2026, 10, 1), date(2026, 10, 8), 0.4
    )
    assert not report.under_ceiling
    assert "OVER the 0 USD ceiling" in summarise(report)


def test_history_follows_every_page():
    client = FakeCostExplorer({"2026-09": SEPTEMBER, "2026-10": OCTOBER}, page_size=1)
    months = history(client, date(2026, 10, 8))
    assert [(item.month, item.usage_usd, item.estimated) for item in months] == [
        ("2026-09", 0.827, False),
        ("2026-10", 0.464, True),
    ]


def test_the_view_carries_the_report_and_the_history():
    report = CostReport(
        month="2026-10",
        start=date(2026, 10, 1),
        end=date(2026, 10, 8),
        estimated=True,
        usage_usd=0.464,
        credits_usd=-0.464,
        tagged_usd=0.2225,
        ceiling_usd=20,
        services=[ServiceCost(service="Amazon S3", usage_usd=0.464, tagged_usd=0.2225)],
    )
    client = FakeCostExplorer({"2026-09": SEPTEMBER, "2026-10": OCTOBER})
    view = cost_view(
        report, history(client, date(2026, 10, 8)), "run-1", datetime(2026, 10, 7, tzinfo=UTC)
    )
    payload = view.model_dump(mode="json")
    assert payload["view"] == "cost_view"
    assert payload["run_id"] == "run-1"
    assert payload["untagged_usd"] == pytest.approx(0.2415)
    # the exclusive end is a query detail; the page shows the last day counted
    assert payload["through"] == "2026-10-07"
    assert payload["under_ceiling"] is True
    assert [item["month"] for item in payload["history"]] == ["2026-09", "2026-10"]


def test_the_summary_names_the_window_and_the_gap():
    report = fetch(FakeCostExplorer({"2026-10": OCTOBER}), date(2026, 10, 1), date(2026, 10, 8), 20)
    text = summarise(report)
    assert "2026-10-01 to 2026-10-07" in text
    assert "estimated" in text
    assert "untagged 0.24" in text
    assert "under the 20 USD ceiling" in text
