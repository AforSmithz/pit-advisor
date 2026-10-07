from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from pydantic import BaseModel

from pitadvisor.outputs.view_contracts import SCHEMA_VERSION

# the account hosts nothing but this project, so its whole bill is the project's bill. the tag
# still matters: bedrock invocations never carry one, and the gap is worth seeing
TAG_KEY: Final = "project"
TAG_VALUE: Final = "pit-advisor"
METRIC: Final = "UnblendedCost"
FIRST_MONTH: Final = date(2026, 9, 1)


class ServiceCost(BaseModel, frozen=True):
    service: str
    usage_usd: float
    tagged_usd: float


class MonthTotal(BaseModel, frozen=True):
    month: str
    usage_usd: float
    credits_usd: float
    estimated: bool


class CostReport(BaseModel, frozen=True):
    month: str
    start: date
    end: date
    estimated: bool
    usage_usd: float
    credits_usd: float
    tagged_usd: float
    ceiling_usd: float
    services: list[ServiceCost]

    @property
    def under_ceiling(self) -> bool:
        return self.usage_usd < self.ceiling_usd

    @property
    def untagged_usd(self) -> float:
        return round(self.usage_usd - self.tagged_usd, 4)

    @property
    def through(self) -> date:
        return self.end - timedelta(days=1)


class CostView(BaseModel, frozen=True):
    view: str = "cost_view"
    schema_version: str = SCHEMA_VERSION
    generated_at: datetime
    run_id: str
    month: str
    start: date
    end: date
    through: date
    estimated: bool
    usage_usd: float
    credits_usd: float
    tagged_usd: float
    untagged_usd: float
    ceiling_usd: float
    under_ceiling: bool
    services: list[ServiceCost]
    history: list[MonthTotal]


def _first_of(day: date) -> date:
    return day.replace(day=1)


def _next_month(day: date) -> date:
    return (_first_of(day) + timedelta(days=32)).replace(day=1)


def period(month: str, today: date) -> tuple[date, date]:
    """Start and exclusive end of a month, cut at tomorrow so the current one stays queryable."""
    if month == "current":
        start = _first_of(today)
    else:
        try:
            start = datetime.strptime(month, "%Y-%m").date()
        except ValueError as exc:
            raise ValueError(f"month is 'current' or YYYY-MM, not {month!r}") from exc
    if start > today:
        raise ValueError(f"{start:%Y-%m} has not started yet")
    return start, min(_next_month(start), today + timedelta(days=1))


def _amount(metrics: dict[str, Any]) -> float:
    return float(metrics[METRIC]["Amount"])


def _query(
    client: Any,
    start: date,
    end: date,
    group_by: list[dict[str, str]],
    usage_only: bool = True,
    tagged: bool = False,
) -> list[dict[str, Any]]:
    filters: list[dict[str, Any]] = []
    if usage_only:
        filters.append({"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Usage"]}})
    if tagged:
        filters.append({"Tags": {"Key": TAG_KEY, "Values": [TAG_VALUE]}})
    params: dict[str, Any] = {
        "TimePeriod": {"Start": start.isoformat(), "End": end.isoformat()},
        "Granularity": "MONTHLY",
        "Metrics": [METRIC],
        "GroupBy": group_by,
    }
    if len(filters) == 1:
        params["Filter"] = filters[0]
    elif filters:
        params["Filter"] = {"And": filters}
    periods: list[dict[str, Any]] = []
    token: str | None = None
    while True:
        response: dict[str, Any] = client.get_cost_and_usage(
            **params, **({"NextPageToken": token} if token else {})
        )
        periods.extend(response.get("ResultsByTime", []))
        token = response.get("NextPageToken")
        if not token:
            return periods


def _by_key(periods: list[dict[str, Any]]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for item in periods:
        for group in item.get("Groups", []):
            key = str(group["Keys"][0])
            totals[key] = totals.get(key, 0.0) + _amount(group["Metrics"])
    return totals


def fetch(client: Any, start: date, end: date, ceiling_usd: float) -> CostReport:
    by_record = _query(
        client, start, end, [{"Type": "DIMENSION", "Key": "RECORD_TYPE"}], usage_only=False
    )
    records = _by_key(by_record)
    services = _by_key(_query(client, start, end, [{"Type": "DIMENSION", "Key": "SERVICE"}]))
    tagged = _by_key(
        _query(client, start, end, [{"Type": "DIMENSION", "Key": "SERVICE"}], tagged=True)
    )
    rows = [
        ServiceCost(
            service=name,
            usage_usd=round(amount, 4),
            tagged_usd=round(tagged.get(name, 0.0), 4),
        )
        for name, amount in services.items()
        if round(amount, 4) > 0
    ]
    rows.sort(key=lambda row: (-row.usage_usd, row.service))
    return CostReport(
        month=f"{start:%Y-%m}",
        start=start,
        end=end,
        estimated=any(bool(item.get("Estimated")) for item in by_record),
        usage_usd=round(records.get("Usage", 0.0), 4),
        credits_usd=round(records.get("Credit", 0.0), 4),
        tagged_usd=round(sum(tagged.values()), 4),
        ceiling_usd=ceiling_usd,
        services=rows,
    )


def history(client: Any, end: date, since: date = FIRST_MONTH) -> list[MonthTotal]:
    periods = _query(
        client, since, end, [{"Type": "DIMENSION", "Key": "RECORD_TYPE"}], usage_only=False
    )
    months: list[MonthTotal] = []
    for item in periods:
        groups = {
            str(group["Keys"][0]): _amount(group["Metrics"]) for group in item.get("Groups", [])
        }
        months.append(
            MonthTotal(
                month=str(item["TimePeriod"]["Start"])[:7],
                usage_usd=round(groups.get("Usage", 0.0), 4),
                credits_usd=round(groups.get("Credit", 0.0), 4),
                estimated=bool(item.get("Estimated")),
            )
        )
    return months


def cost_view(
    report: CostReport,
    months: list[MonthTotal],
    run_id: str,
    generated_at: datetime | None = None,
) -> CostView:
    return CostView(
        generated_at=generated_at or datetime.now(UTC),
        run_id=run_id,
        month=report.month,
        start=report.start,
        end=report.end,
        through=report.through,
        estimated=report.estimated,
        usage_usd=report.usage_usd,
        credits_usd=report.credits_usd,
        tagged_usd=report.tagged_usd,
        untagged_usd=report.untagged_usd,
        ceiling_usd=report.ceiling_usd,
        under_ceiling=report.under_ceiling,
        services=report.services,
        history=months,
    )


def summarise(report: CostReport) -> str:
    status = "estimated, month still open" if report.estimated else "final"
    lines = [
        f"cost for {report.month}, {report.start} to {report.through} ({status})",
        f"  {'usage before credits':<28}{report.usage_usd:>9.2f} USD",
        f"  {'credits applied':<28}{report.credits_usd:>9.2f} USD",
        f"  {f'tagged {TAG_KEY}={TAG_VALUE}':<28}{report.tagged_usd:>9.2f} USD, "
        f"untagged {report.untagged_usd:.2f}",
        "",
    ]
    for row in report.services:
        lines.append(f"  {row.service:<56} {row.usage_usd:>8.4f}")
    verdict = "under" if report.under_ceiling else "OVER"
    lines.extend(["", f"{verdict} the {report.ceiling_usd:.0f} USD ceiling", ""])
    return "\n".join(lines)
