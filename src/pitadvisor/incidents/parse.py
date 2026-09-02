import re
from datetime import datetime, time
from enum import StrEnum
from typing import Final

from pydantic import BaseModel

# the pdf text layer separates every word with a non-breaking space, so a span stored before
# normalising will not match the text it was taken from
NBSP: Final = "\u00a0"
# the pdf uses typographic punctuation and a reader copying a quote out of it types the ascii
TYPOGRAPHIC: Final = {
    "\u2018": "'",
    "\u2019": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
}

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
# the stewards spell each book several ways: with and without the FIA prefix, "Formula One" and
# "Formula 1", the technical regulations sometimes without either, and a season in front of any
# of them. one book each or the citations will not join
BOOK = re.compile(
    r"(?:(?P<edition>(?:19|20)\d{2})\s+)?(?:FIA\s+)?"
    r"(?:(?P<isc>International\s+Sporting\s+Code)"
    r"|(?:Formula\s+(?:One|1)\s+)?(?P<kind>Sporting|Technical)\s+Regulations)",
    re.I,
)
CITATION = re.compile(
    r"(Appendix\s+[A-Z](?:\s+Ch\s+[IVXLC]+)?(?:\s+(?:Art\.?\s*)?\d+(?:\.\d+)*(?:\.?[a-z])?)?"
    r"|(?:Article|Art)\.?\s*\d+(?:\.\d+)*(?:\s*[a-z]\))?)"
)
SUMMONED = re.compile(r"summoned\s*\(documents?\s+([\d\s&,and]+)\)", re.I)
NUMBER = re.compile(r"\d+")


class Span(BaseModel, frozen=True):
    field: str
    text: str


class Book(StrEnum):
    SPORTING = "sporting"
    TECHNICAL = "technical"
    ISC = "isc"


class Article(BaseModel, frozen=True):
    code: str
    regulation: str
    book: Book | None = None
    edition: int | None = None


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
    # against have to be folded the same way or every multi line field fails verification. the
    # wrapped line is indented, so the run of spaces it leaves behind is squeezed after the join
    # and not before, or a quote copied by a reader with single spaces will not be found
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n(?!\n)", " ", normalize(text)))


def _fold(text: str) -> tuple[str, list[int]]:
    kept: list[str] = []
    offsets: list[int] = []
    for index, character in enumerate(text):
        if character.isspace():
            continue
        kept.append(TYPOGRAPHIC.get(character, character))
        offsets.append(index)
    return "".join(kept), offsets


def locate(quote: str, source: str) -> str | None:
    # the pdf breaks a word across a space and prints a curly apostrophe, and a reader copying a
    # quote out of it will not reproduce either. matching without whitespace or smart punctuation
    # and then slicing the source keeps what we store a literal substring of the document
    needle, _ = _fold(quote)
    if not needle:
        return None
    if quote in source:
        return quote
    folded, offsets = _fold(source)
    at = folded.find(needle)
    if at < 0:
        return None
    return source[offsets[at] : offsets[at + len(needle) - 1] + 1]


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


def _books(charge: str) -> list[tuple[int, int, Book, str, int | None]]:
    found: list[tuple[int, int, Book, str, int | None]] = []
    for match in BOOK.finditer(charge):
        if match.group("isc"):
            which = Book.ISC
        else:
            which = Book.SPORTING if match.group("kind").lower() == "sporting" else Book.TECHNICAL
        edition = match.group("edition")
        found.append(
            (
                match.start(),
                match.end(),
                which,
                match.group(0).strip(),
                int(edition) if edition else None,
            )
        )
    return found


def articles(charge: str | None) -> list[Article]:
    if not charge:
        return []
    books = _books(charge)

    # a charge that names two books cites both, so a citation takes the book named after it and
    # falls back to the one before when the charge leads with the book instead of trailing it
    def owner(at: int) -> tuple[Book, str, int | None] | None:
        after = [b for b in books if b[1] > at]
        chosen = after[0] if after else (books[-1] if books else None)
        return (chosen[2], chosen[3], chosen[4]) if chosen else None

    # a charge cites appendices and abbreviated articles as often as "Article 31.5", so the code
    # is kept exactly as the stewards wrote it rather than normalised into a shape it never had
    articles: list[Article] = []
    for found in CITATION.finditer(charge):
        if any(start <= found.start() < end for start, end, _, _, _ in books):
            continue
        which = owner(found.start())
        articles.append(
            Article(
                code=found.group(0).strip(),
                regulation=which[1] if which else "",
                book=which[0] if which else None,
                edition=which[2] if which else None,
            )
        )
    return articles


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
        articles=articles(charge),
        outcome=fields.get("Decision"),
        reason=reason,
        summoned=[int(n) for n in NUMBER.findall(summons.group(1))] if summons else [],
        spans=spans,
    )


def verify(decision: Decision, raw: str) -> list[str]:
    text = collapse(raw)
    return [span.field for span in decision.spans if span.text not in text]
