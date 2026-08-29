from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# athena engine v3 is trino, and parsing it as anything else quietly accepts syntax the
# engine would reject and rejects syntax it would take
DIALECT: Final = "trino"

ALLOWED_TABLES: Final = (
    "gold_race_results",
    "gold_qualifying_gaps",
    "gold_pit_stop_summary",
)
MAX_LIMIT: Final = 200


class RejectedError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def guard(sql: str, allowed: tuple[str, ...] = ALLOWED_TABLES, limit: int = MAX_LIMIT) -> str:
    statement = _one_statement(sql)
    _no_writes(statement)
    _tables_allowed(statement, allowed)
    return _capped(statement, limit).sql(dialect=DIALECT, comments=False)


def _one_statement(sql: str) -> exp.Select:
    try:
        parsed = [item for item in sqlglot.parse(sql, dialect=DIALECT) if item is not None]
    except ParseError as exc:
        raise RejectedError(f"will not parse as {DIALECT}: {exc}") from exc
    if not parsed:
        raise RejectedError("empty query")
    if len(parsed) > 1:
        raise RejectedError(f"{len(parsed)} statements, only one is allowed")
    statement = parsed[0]
    if not isinstance(statement, exp.Select):
        raise RejectedError(f"only a single select is allowed, got {_describe(statement)}")
    return statement


def _describe(statement: object) -> str:
    if isinstance(statement, exp.Command):
        return str(statement.this).lower()
    return type(statement).__name__.lower()


def _no_writes(statement: exp.Select) -> None:
    # select ... into is a write wearing a select's clothes, and trino parses it as one
    for node in statement.find_all(exp.Into, exp.Command):
        raise RejectedError(f"{_describe(node)} is not readable sql")


def _tables_allowed(statement: exp.Select, allowed: tuple[str, ...]) -> None:
    permitted = {name.lower() for name in allowed}
    local = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
    for table in statement.find_all(exp.Table):
        name = table.name.lower()
        if table.catalog or table.db:
            raise RejectedError(f"{table.sql(dialect=DIALECT)} names a catalog or schema")
        if name in local:
            continue
        if name not in permitted:
            raise RejectedError(f"{name} is not an allowlisted mart")


def _capped(statement: exp.Select, limit: int) -> exp.Select:
    existing = statement.args.get("limit")
    if existing is None:
        return statement.limit(limit)
    value = existing.expression
    if not isinstance(value, exp.Literal) or not value.is_int:
        raise RejectedError("limit must be a plain integer")
    return statement if int(value.name) <= limit else statement.limit(limit)
