import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import yaml
from pydantic import BaseModel, Field

from pitadvisor.agent import tools
from pitadvisor.agent.runtime import Agent, Answer
from pitadvisor.agent.tools import Toolbox

REPORT: Final = "evals.json"
SUMMARY: Final = "summary.txt"
# the gate in CLAUDE.md §7. a suite may tighten these, never loosen them
GATE: Final = {
    "numeric_exact_match": 0.95,
    "retrieval_hit_rate": 0.90,
    "tool_selection": 0.90,
    "ungrounded_figures": 0.0,
}
DECLINED = (
    "do not have",
    "don't have",
    "not have",
    "cannot",
    "can't",
    "will not",
    "won't",
    "do not advise",
    "don't advise",
    "does not advise",
    "not available",
    "no tool",
    "not something",
    "outside what",
    "outside the",
    "would need",
    "is not in the",
    "covers only",
    "not in the corpus",
    "no data",
)


class SuiteError(RuntimeError):
    pass


class Truth(BaseModel, frozen=True):
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    path: str | None = None
    sql: str | None = None
    column: str | None = None
    row: int = 0


class Case(BaseModel, frozen=True):
    id: str
    question: str
    kind: str = "lookup"
    expect_tools: list[str] = Field(default_factory=list)
    numeric: Truth | None = None
    must_cite: str | None = None
    refuse: bool = False


class Suite(BaseModel, frozen=True):
    thresholds: dict[str, float] = Field(default_factory=lambda: dict(GATE))
    cases: list[Case]


class CaseScore(BaseModel, frozen=True):
    id: str
    kind: str
    question: str
    answer: str
    tools_used: list[str]
    tool_ok: bool
    numeric_ok: bool | None
    citation_ok: bool | None
    refusal_ok: bool | None
    grounded: bool
    ungrounded: list[str]
    expected: str | None = None
    detail: str = ""


class Report(BaseModel, frozen=True):
    generated_at: datetime
    model_id: str
    run_id: str
    cases: list[CaseScore]
    thresholds: dict[str, float]
    scores: dict[str, float]
    counts: dict[str, int]
    usage: dict[str, int]
    passed: bool


def load(path: Path) -> Suite:
    if not path.exists():
        raise SuiteError(f"{path} is not there")
    payload = yaml.safe_load(path.read_text())
    suite = Suite.model_validate(payload)
    loosened = [name for name, value in suite.thresholds.items() if _loosens(name, value)]
    if loosened:
        raise SuiteError(f"the suite loosens the gate on {', '.join(sorted(loosened))}")
    return suite


def _loosens(name: str, value: float) -> bool:
    if name not in GATE:
        return False
    return value < GATE[name] if name != "ungrounded_figures" else value > GATE[name]


def expected_value(box: Toolbox, truth: Truth) -> float | str:
    if truth.sql is not None:
        result = tools.invoke(box, "query_marts", {"sql": truth.sql})
        if not result.ok:
            raise SuiteError(f"the ground-truth query failed: {result.detail}")
        rows = result.payload["rows"]
        if truth.row >= len(rows):
            raise SuiteError(f"the ground-truth query returned {len(rows)} rows")
        return rows[truth.row][truth.column]
    if truth.tool is None or truth.path is None:
        raise SuiteError("a numeric expectation needs either sql or tool and path")
    result = tools.invoke(box, truth.tool, truth.arguments)
    if not result.ok:
        raise SuiteError(f"the ground-truth tool failed: {result.detail}")
    return _at(result.payload, truth.path)


def _at(payload: Any, path: str) -> Any:
    node = payload
    for part in path.split("."):
        node = node[int(part)] if part.isdigit() else node[part]
    return node


def _quoted(answer: str, value: float | str) -> bool:
    if isinstance(value, str):
        return value.lower() in answer.lower()
    # markdown emphasis sits between the sign and the digits often enough to matter
    plain = answer.replace("*", "").replace("_", "")
    found = {
        match.replace(",", "").lstrip("+")
        for match in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", plain)
    }
    return bool(found & tools.renderings(float(value)))


def score(case: Case, answer: Answer, box: Toolbox) -> CaseScore:
    used = answer.tools_used
    tool_ok = set(case.expect_tools) <= set(used) if case.expect_tools else not used
    numeric_ok: bool | None = None
    expected: str | None = None
    detail = ""
    if case.numeric is not None:
        try:
            value = expected_value(box, case.numeric)
            expected = str(value)
            numeric_ok = _quoted(answer.text, value)
        except SuiteError as exc:
            numeric_ok = False
            detail = str(exc)
    citation_ok = None if case.must_cite is None else _cited(answer, case.must_cite)
    refusal_ok = None if not case.refuse else _declined(answer)
    return CaseScore(
        id=case.id,
        kind=case.kind,
        question=case.question,
        answer=answer.text,
        tools_used=used,
        tool_ok=tool_ok,
        numeric_ok=numeric_ok,
        citation_ok=citation_ok,
        refusal_ok=refusal_ok,
        grounded=answer.grounded,
        ungrounded=answer.ungrounded,
        expected=expected,
        detail=detail,
    )


def _cited(answer: Answer, wanted: str) -> bool:
    return wanted.lower() in answer.text.lower()


def _declined(answer: Answer) -> bool:
    lowered = answer.text.lower().replace("\u2019", "'")
    return any(marker in lowered for marker in DECLINED) or answer.refused


def _rate(values: list[bool]) -> float:
    return sum(values) / len(values) if values else 1.0


def run(
    agent: Agent,
    box: Toolbox,
    suite: Suite,
    run_id: str,
    pace_seconds: float = 2.0,
    sleep: Callable[[float], None] = time.sleep,
    on_case: Callable[[CaseScore], None] | None = None,
) -> Report:
    scores: list[CaseScore] = []
    usage: dict[str, int] = {}
    for index, case in enumerate(suite.cases):
        if index and pace_seconds:
            # the model quota is per minute and a golden set is a burst, so the run paces
            # itself rather than relying on the retry to absorb the whole thing
            sleep(pace_seconds)
        try:
            answer = agent.ask(case.question)
        except Exception as exc:
            scores.append(_unanswered(case, exc))
            if on_case is not None:
                on_case(scores[-1])
            continue
        for key, value in answer.usage.items():
            usage[key] = usage.get(key, 0) + value
        scores.append(score(case, answer, box))
        if on_case is not None:
            on_case(scores[-1])
    return report_of(scores, suite, agent.model_id, run_id, usage)


def _unanswered(case: Case, exc: Exception) -> CaseScore:
    return CaseScore(
        id=case.id,
        kind=case.kind,
        question=case.question,
        answer="",
        tools_used=[],
        tool_ok=False,
        numeric_ok=False if case.numeric else None,
        citation_ok=False if case.must_cite else None,
        refusal_ok=False if case.refuse else None,
        grounded=True,
        ungrounded=[],
        detail=f"{type(exc).__name__}: {exc}",
    )


def report_of(
    scores: list[CaseScore],
    suite: Suite,
    model_id: str,
    run_id: str,
    usage: dict[str, int],
) -> Report:
    measured = {
        "numeric_exact_match": _rate(
            [item.numeric_ok for item in scores if item.numeric_ok is not None]
        ),
        "retrieval_hit_rate": _rate(
            [item.citation_ok for item in scores if item.citation_ok is not None]
        ),
        "tool_selection": _rate([item.tool_ok for item in scores]),
        "refusals": _rate([item.refusal_ok for item in scores if item.refusal_ok is not None]),
        "ungrounded_figures": float(sum(1 for item in scores if not item.grounded)),
    }
    counts = {
        "cases": len(scores),
        "numeric": sum(1 for item in scores if item.numeric_ok is not None),
        "retrieval": sum(1 for item in scores if item.citation_ok is not None),
        "refusal": sum(1 for item in scores if item.refusal_ok is not None),
    }
    return Report(
        generated_at=datetime.now(UTC),
        model_id=model_id,
        run_id=run_id,
        cases=scores,
        thresholds=suite.thresholds,
        scores=measured,
        counts=counts,
        usage=usage,
        passed=passes(measured, suite.thresholds),
    )


def passes(measured: dict[str, float], thresholds: dict[str, float]) -> bool:
    for name, floor in thresholds.items():
        value = measured.get(name)
        if value is None:
            continue
        if name == "ungrounded_figures":
            if value > floor:
                return False
        elif value < floor:
            return False
    return True


def summarise(report: Report) -> str:
    lines = [
        f"model     {report.model_id}",
        f"cases     {report.counts['cases']}",
        "",
    ]
    for name, value in sorted(report.scores.items()):
        floor = report.thresholds.get(name)
        if name == "ungrounded_figures":
            mark = "ok  " if floor is None or value <= floor else "FAIL"
            lines.append(f"{mark}  {name:<20} {int(value)}")
            continue
        mark = "ok  " if floor is None or value >= floor else "FAIL"
        target = f"  (needs {floor:.0%})" if floor is not None else ""
        lines.append(f"{mark}  {name:<20} {value:.1%}{target}")
    unmet = [item for item in report.cases if failed(item)]
    if unmet:
        lines.append("")
        lines.append("failed cases")
        for item in unmet:
            lines.append(f"  {item.id:<16} {item.detail or _why(item)}")
    lines.append("")
    lines.append("passed" if report.passed else "did not pass the gate")
    return "\n".join(lines) + "\n"


def failed(item: CaseScore) -> bool:
    return (
        not item.tool_ok
        or item.numeric_ok is False
        or item.citation_ok is False
        or item.refusal_ok is False
        or not item.grounded
    )


def _why(item: CaseScore) -> str:
    if not item.tool_ok:
        return f"used {', '.join(item.tools_used) or 'no tool'}"
    if item.numeric_ok is False:
        return f"expected {item.expected} in the answer"
    if item.citation_ok is False:
        return "no citation"
    if item.refusal_ok is False:
        return "answered a question it should have declined"
    return f"ungrounded figures: {', '.join(item.ungrounded)}"
