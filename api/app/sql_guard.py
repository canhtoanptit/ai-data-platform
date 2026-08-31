"""Validates LLM-written SQL before it is allowed anywhere near the warehouse.

This is the security boundary of the chat feature. An LLM is an untrusted
author: it is not adversarial, but it is *steerable*, and the question it works
from arrives over HTTP. So the rule is not "the model usually writes SELECTs" —
it is "nothing but a single SELECT can leave this module".

**Why a parser and not a regex.** Every regex allow-list for SQL has the same
hole: it reasons about text, while the database reasons about a parse tree.
`select 1; drop table x`, a `-- comment` hiding a second statement, string
literals containing the word `delete`, unusual whitespace, `/*!*/` — all of them
turn a "starts with select" check into a false negative or a false positive.
sqlglot parses to the same shape Postgres will, so the checks below are about
what the statement *is*, not how it is spelled.

Three checks, in order:

1. **Exactly one statement.** Multi-statement text is rejected outright rather
   than trimmed to the first: "run the first half of what the model wrote" is
   never the safe reading of an unexpected payload.
2. **That statement is a SELECT.** CTEs, subqueries, UNIONs and joins are all
   fine — they are still reads. Anything with a write or DDL node in it is not.
   The check walks the whole tree, so a write hidden inside a CTE
   (`with x as (delete from t returning *) select * from x`, which Postgres
   really does support) is caught.
3. **A LIMIT the caller controls.** Missing → added; too large → clamped. This
   bounds the answer, not just the response size: the chat UI renders rows.

Blocking `pg_*` and `information_schema` is defence in depth rather than a
confidentiality boundary — the connection is a read-only mart reader — but a
question that reaches for the system catalogs is not a question about
collections data, and answering it is out of scope for this endpoint.

Everything here is a pure function: text in, text or an error out. No config, no
database, no network — which is what makes it exhaustively unit-testable, and
this file is where the tests are worth the most.
"""

from __future__ import annotations

import sqlglot
from sqlglot import exp

# Postgres locally and the same dialect family in Snowflake's eyes for the SQL
# this generates. Parsing with the *target* dialect matters: `::` casts and
# `filter (where ...)` are syntax errors in sqlglot's default dialect.
DIALECT = "postgres"

# Ceiling on rows, applied to the SQL itself rather than only to the fetch loop.
# A LIMIT lets Postgres stop early; a fetch cap only stops us reading a result
# the database already materialised.
MAX_ROWS = 100

# Schemas that are never a legitimate target for a question about collections.
BLOCKED_SCHEMAS = frozenset({"information_schema", "pg_catalog", "pg_toast"})

# Table/function name prefixes with the same problem (pg_stat_activity,
# pg_read_file, pg_sleep, ...). Checked as a prefix because the catalog is a
# large and growing family of names.
BLOCKED_PREFIXES = ("pg_",)

# Expression types that read nothing and change something. `exp.Command` is
# sqlglot's catch-all for statements it does not model in detail (COPY, GRANT,
# VACUUM, SET, CALL, ...), which is exactly the set we want to refuse: if the
# parser cannot tell us what it does, it does not run.
FORBIDDEN_NODES: tuple[type[exp.Expression], ...] = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Grant,
    exp.Command,
    exp.Transaction,
    exp.Commit,
    exp.Rollback,
    exp.Merge,
    exp.Copy,
)


class UnsafeSql(ValueError):
    """The statement is not a single, read-only SELECT.

    A ValueError subclass rather than an HTTPException: this module knows the
    SQL is wrong, not what an HTTP client should be told about it. The chat
    router does the mapping (422, with the rejected SQL, so the UI can show what
    the model tried).
    """


def _statement_kind(statement: exp.Expression) -> str:
    """A human-readable name for what the model wrote, for the error message."""
    return type(statement).__name__.upper()


def _reject_forbidden_nodes(statement: exp.Expression) -> None:
    for node in statement.walk():
        if isinstance(node, FORBIDDEN_NODES):
            raise UnsafeSql(
                f"only SELECT statements are allowed; found {_statement_kind(node)}"
            )


def _reject_blocked_tables(statement: exp.Expression) -> None:
    """Refuse the Postgres system catalogs, however they are spelled.

    Checks the parsed table node, so `PG_CATALOG.PG_TABLES`, a quoted
    `"pg_class"` and an unqualified `pg_stat_activity` are all the same thing
    here. Function calls are checked too, because `pg_read_file(...)` and
    `pg_sleep(...)` are reachable without naming a table at all.
    """
    for table in statement.find_all(exp.Table):
        schema = (table.text("db") or "").lower()
        name = (table.name or "").lower()
        if schema in BLOCKED_SCHEMAS or name.startswith(BLOCKED_PREFIXES):
            raise UnsafeSql(
                f"querying {schema + '.' if schema else ''}{name} is not allowed; "
                "ask about the analytics_marts tables"
            )
    for func in statement.find_all(exp.Anonymous):
        if (func.name or "").lower().startswith(BLOCKED_PREFIXES):
            raise UnsafeSql(f"calling {func.name}() is not allowed")


def _current_limit(statement: exp.Expression) -> int | None:
    """The outer query's LIMIT as an int, or None if it has none or is dynamic.

    A LIMIT on an inner subquery is deliberately ignored: it bounds that
    subquery, not the rows we would return. A non-literal LIMIT (a parameter or
    an expression) reads as "no usable limit" and gets replaced.
    """
    limit = statement.args.get("limit")
    if limit is None:
        return None
    value = limit.expression
    if isinstance(value, exp.Literal) and value.is_int:
        return int(value.name)
    return None


def _apply_limit(statement: exp.Expression, max_rows: int) -> exp.Expression:
    """Ensure the outer query returns at most `max_rows`.

    `.limit(n)` on a SELECT or a set operation replaces the existing clause, so
    the same call covers "add one" and "clamp the one that is there".
    """
    current = _current_limit(statement)
    if current is not None and current <= max_rows:
        return statement
    return statement.limit(max_rows)


def validate(sql: str, max_rows: int = MAX_ROWS) -> str:
    """Return safe, row-limited SQL, or raise UnsafeSql.

    The returned string is re-generated from the parse tree rather than being
    the caller's text with a LIMIT stapled on. That is the point: what runs is
    what the parser understood, so anything the parser silently dropped (a
    trailing fragment, a comment) cannot come along for the ride.
    """
    if not sql or not sql.strip():
        raise UnsafeSql("no SQL to run")

    try:
        statements = [
            statement
            for statement in sqlglot.parse(sql, dialect=DIALECT)
            # sqlglot yields None for an empty segment, which is what a trailing
            # semicolon produces. `select 1;` is one statement, not two.
            if statement is not None
        ]
    except sqlglot.ParseError as exc:
        raise UnsafeSql(f"could not parse the SQL: {exc}") from exc

    if not statements:
        raise UnsafeSql("no SQL to run")
    if len(statements) > 1:
        raise UnsafeSql(
            f"expected exactly one statement, got {len(statements)}; "
            "multiple statements are not allowed"
        )

    statement = statements[0]

    # A WITH-wrapped query parses as the inner Select/SetOperation carrying a
    # `with` arg, so this covers CTEs without unwrapping anything. Set
    # operations (UNION/INTERSECT/EXCEPT) are reads too, and both branches were
    # already walked for forbidden nodes below.
    if not isinstance(statement, (exp.Select, exp.SetOperation, exp.Subquery)):
        raise UnsafeSql(
            f"only SELECT statements are allowed; got {_statement_kind(statement)}"
        )

    _reject_forbidden_nodes(statement)
    _reject_blocked_tables(statement)

    limited = _apply_limit(statement, max_rows)
    return limited.sql(dialect=DIALECT, pretty=True)


# Fences are the single most common thing an LLM adds to SQL it was told to
# return bare, so stripping them is part of reading the response, not part of
# validation. It lives here because it is the same pure text-in/text-out shape
# and it is tested alongside the guard.
def strip_code_fences(text: str) -> str:
    """Pull the SQL out of a ```sql ... ``` block, if the model used one."""
    cleaned = text.strip()
    if not cleaned.startswith("```"):
        return cleaned
    # Drop the opening fence and its optional language tag, then everything from
    # the closing fence onward (models sometimes add prose after it).
    body = cleaned[3:]
    newline = body.find("\n")
    if newline != -1 and body[:newline].strip().isalpha():
        body = body[newline + 1 :]
    closing = body.find("```")
    if closing != -1:
        body = body[:closing]
    return body.strip()
