import re
from datetime import datetime, time
from typing import Final

from pydantic import BaseModel

# the pdf text layer separates every word with a non-breaking space, so a span stored before
# normalising will not match the text it was taken from
NBSP: Final = "\u00a0"

# 2021 and 2022 label the charge "Offence" and 2023 onwards labels it "Infringement", in the
# document title and in the field block both. reading only one of them loses two seasons
CHARGE_LABELS: Final = ("Offence", "Infringement")
FIELDS: Final = (
    "No / Driver",
    "Competitor",
    "Time",
    "Session",
    "Fact",
    *CHARGE_LABELS,
    "Decision",
    "Reason",
)

DOCUMENT = re.compile(r"^Document\s+(\d+)\s*$", re.M)
ISSUED_DATE = re.compile(r"^Date\s+(\d{1,2} \w+ \d{4})\s*$", re.M)
ISSUED_TIME = re.compile(r"^Time\s+(\d{1,2}:\d{2})\s*$", re.M)
DRIVER = re.compile(r"^(\d+)\s*-\s*(.+)$")
# the charge often runs on past the rulebook name into what the driver did, so the three books
# the stewards actually cite are matched first and the open ended capture is the fallback
KNOWN_BOOK = re.compile(
    r"\bof\s+the\s+((?:FIA\s+)?(?:Formula\s+One\s+Sporting\s+Regulations"
    r"|Formula\s+One\s+Technical\s+Regulations|International\s+Sporting\s+Code))",
    re.I,
)
RULEBOOK = re.compile(r"\bof\s+the\s+(.+?)\.?\s*$", re.S)
CITATION = re.compile(
    r"(Appendix\s+[A-Z](?:\s+Ch\s+[IVXLC]+)?(?:\s+(?:Art\.?\s*)?\d+(?:\.\d+)*(?:\.?[a-z])?)?"
    r"|(?:Article|Art)\.?\s*\d+(?:\.\d+)*(?:\s*[a-z]\))?)"
)
SUMMONED = re.compile(r"summoned\s*\(documents?\s+([\d\s&,and]+)\)", re.I)
NUMBER = re.compile(r"\d+")


class Span(BaseModel, frozen=True):
    field: str
    text: str


class Article(BaseModel, frozen=True):
    code: str
    regulation: str


class Decision(BaseModel, frozen=True):
    document: int | None = None
    issued: datetime | None = None
    car: int | None = None
    driver: str | None = None
    competitor: str | None = None
    session: str | None = None
    fact: str | None = None
    charge: str | None = None
    articles: list[Article] = []
    outcome: str | None = None
    reason: str | None = None
    summoned: list[int] = []
    spans: list[Span] = []

    @property
    def structured(self) -> bool:
        return self.outcome is not None


def normalize(text: str) -> str:
    return re.sub(r"[ \t]{2,}", " ", text.replace(NBSP, " "))


def collapse(text: str) -> str:
    # a field value wraps mid sentence in the pdf, so a stored span and the text it is checked
    # against have to be folded the same way or every multi line field fails verification
    return re.sub(r"\n(?!\n)", " ", normalize(text))


def _issued(text: str) -> datetime | None:
    day, clock = ISSUED_DATE.search(text), ISSUED_TIME.search(text)
    if day is None:
        return None
    stamp = datetime.strptime(day.group(1), "%d %B %Y").date()
    if clock is None:
        return datetime.combine(stamp, time())
    hour, minute = (int(part) for part in clock.group(1).split(":"))
    return datetime.combine(stamp, time(hour, minute))


def _block(text: str) -> dict[str, str]:
    start = text.find("No / Driver")
    if start < 0:
        return {}
    body = text[start:]
    marks: list[tuple[int, str]] = []
    cursor = 0
    for label in FIELDS:
        found = re.search(rf"^{re.escape(label)}\s+", body[cursor:], re.M)
        if found is None:
            continue
        marks.append((cursor + found.start(), label))
        cursor += found.end()
    found_fields: dict[str, str] = {}
    for index, (offset, label) in enumerate(marks):
        end = marks[index + 1][0] if index + 1 < len(marks) else len(body)
        found_fields[label] = collapse(body[offset + len(label) : end]).strip()
    return found_fields


def _tail(reason: str) -> str:
    # every decision closes with the same appeal boilerplate and the stewards' signatures
    cut = re.search(r"Competitors are reminded that they have the right to appeal", reason)
    return reason[: cut.start()].strip() if cut else reason


def _articles(charge: str | None) -> list[Article]:
    if not charge:
        return []
    book = KNOWN_BOOK.search(charge) or RULEBOOK.search(charge)
    # a charge cites appendices and abbreviated articles as often as "Article 31.5", so the code
    # is kept exactly as the stewards wrote it rather than normalised into a shape it never had
    name = book.group(1).strip() if book else ""
    head = charge[: book.start()] if book else charge
    return [Article(code=found.strip(), regulation=name) for found in CITATION.findall(head)]


def parse(raw: str) -> Decision:
    text = normalize(raw)
    fields = _block(text)
    charge = next((fields[label] for label in CHARGE_LABELS if label in fields), None)
    driver_field = fields.get("No / Driver", "")
    driver = DRIVER.match(driver_field)
    reason = _tail(fields["Reason"]) if "Reason" in fields else None
    summons = SUMMONED.search(text)
    document = DOCUMENT.search(text)
    spans = [Span(field=name, text=value) for name, value in fields.items() if value]
    return Decision(
        document=int(document.group(1)) if document else None,
        issued=_issued(text),
        car=int(driver.group(1)) if driver else None,
        driver=driver.group(2).strip() if driver else None,
        competitor=fields.get("Competitor"),
        session=fields.get("Session"),
        fact=fields.get("Fact"),
        charge=charge,
        articles=_articles(charge),
        outcome=fields.get("Decision"),
        reason=reason,
        summoned=[int(n) for n in NUMBER.findall(summons.group(1))] if summons else [],
        spans=spans,
    )


def verify(decision: Decision, raw: str) -> list[str]:
    text = collapse(raw)
    return [span.field for span in decision.spans if span.text not in text]
