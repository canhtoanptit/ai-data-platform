"""Ask the marts a question in English: NL -> SQL -> rows -> prose.

The endpoint is two LLM calls with the warehouse in between:

    question ──► LLM #1 (write SQL, given the dbt-generated schema briefing)
                    │
                    ▼
                 sql_guard.validate  ── rejected ──► retry once with the error,
                    │                                 then 422 with the attempt
                    ▼
                 read-only transaction, 5s timeout, ≤100 rows
                    │
                    ▼
                 LLM #2 (summarise the rows) ── fails ──► answer = null, rows stand

**Untrusted SQL is handled in three independent layers**, none of which relies on
the others being correct:

1. `sql_guard.validate` — parses the statement and refuses anything that is not
   a single SELECT. This is the layer that reasons about *intent*.
2. An explicit `SET TRANSACTION READ ONLY` around the execution. Even if the
   guard were wrong, Postgres itself refuses the write.
3. `SET LOCAL statement_timeout` — a SELECT can be perfectly read-only and still
   be a denial of service (a cross join over the fact table). The timeout bounds
   the damage a *valid* query can do, which neither of the other two layers can.

A fourth, weaker layer sits outside this file: the connection's credentials.
This is a demo warehouse where the API user owns the marts, so read-only
credentials are not available to lean on — which is exactly why the three above
are all enforced in code.

**Single-turn on purpose.** No conversation history is sent to the model and none
is stored: each question is answered from the schema briefing alone. Follow-ups
("and by team?") therefore don't work, and that is a v1 non-goal rather than an
oversight — memory turns a stateless endpoint into a session store, and the
interesting problem here is the SQL, not the dialogue.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import llm
from ..db import DbConn
from ..schema_context import build_schema_context
from ..schemas_chat import ChatRequest, ChatResponse
from ..sql_guard import MAX_ROWS, UnsafeSql, strip_code_fences, validate

router = APIRouter(prefix="/api/chat", tags=["chat"])

# How long a single question's query may run. Small deliberately: every
# reasonable question about a 8-row demo mart answers in milliseconds, so 5s is
# already two orders of magnitude of headroom, and anything slower is a mistake
# worth interrupting rather than waiting for.
STATEMENT_TIMEOUT = "5s"

# Rows sent to the summarising call. Capped well below MAX_ROWS because prose
# over 100 rows is not prose anyone reads, and the tokens are better spent on
# the schema. The table in the UI shows all of them regardless.
ROWS_FOR_SUMMARY = 20

_SQL_SYSTEM_PROMPT = f"""\
You are a SQL analyst. You answer questions by writing ONE PostgreSQL SELECT
statement over the tables described below.

Rules:
- Output SQL only. No prose, no explanation, no markdown code fences, no comments.
- Exactly one statement. SELECT only — never INSERT, UPDATE, DELETE or DDL.
- Always schema-qualify tables as analytics_marts.<table>.
- Only use the tables and columns listed below. Do not invent columns.
- Add a LIMIT of at most {MAX_ROWS} unless the query is a single aggregate row.
- Label computed columns with a readable alias.
- If the question cannot be answered from these tables, write the closest
  SELECT that shows what data does exist rather than refusing.

Tables:
{{schema}}
"""

_ANSWER_SYSTEM_PROMPT = """\
You are a data analyst reporting a query result to a colleague.

Given a question, the SQL that answered it and the rows it returned, reply with
two or three plain sentences stating what the numbers show. Quote the specific
figures. Do not describe the SQL, do not mention tables or columns, and do not
apologise or add caveats about the data. If the result is empty, say so plainly.
"""


def _json_safe(value: Any) -> Any:
    """Convert a driver value into something JSON can carry.

    Postgres hands back `Decimal` for numeric columns and `date`/`datetime` for
    dates, neither of which json can serialise. Decimals become floats because
    these are display figures in a chat answer, not ledger amounts — the same
    call the mart response models already make. Anything unrecognised is
    stringified rather than raising: the SQL is user-shaped, so a column of some
    exotic type must degrade to a readable cell, not a 500.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (dt.date, dt.datetime, dt.time)):
        return value.isoformat()
    return str(value)


def _generate_sql(question: str, schema: str, previous_error: str | None = None) -> str:
    """One SQL-writing call. `previous_error` appends the validator's complaint.

    Retrying with the error is the cheapest accuracy fix available: the common
    failures (a hallucinated column, a stray fence, a trailing explanation) are
    ones the model corrects immediately once told. One retry, not a loop —
    beyond the first, failures are usually the question, not the model, and a
    loop would burn a rate-limited free tier on it.
    """
    prompt = f"Question: {question}"
    if previous_error is not None:
        prompt += (
            f"\n\nYour previous SQL was rejected: {previous_error}\n"
            "Return corrected SQL. SQL only."
        )
    raw = llm.complete(_SQL_SYSTEM_PROMPT.format(schema=schema), prompt)
    return strip_code_fences(raw)


def _run_read_only(conn: DbConn, sql: str) -> tuple[list[str], list[list[Any]]]:
    """Execute validated SQL inside a read-only, time-boxed transaction.

    `SET TRANSACTION READ ONLY` has to be the first thing in the transaction, so
    the explicit `conn.begin()` matters — with SQLAlchemy's autobegin the SELECT
    would open the transaction and the SET would arrive too late to apply to it.
    `SET LOCAL` scopes the timeout to this transaction so it cannot leak onto the
    next request to reuse this pooled connection.
    """
    with conn.begin():
        conn.execute(text("set transaction read only"))
        conn.execute(text(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'"))
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [[_json_safe(value) for value in row] for row in result.fetchmany(MAX_ROWS)]
    return columns, rows


def _summarise(question: str, sql: str, columns: list[str], rows: list[list[Any]]) -> str | None:
    """Prose over the rows, or None if the second LLM call fails.

    Swallowing this failure is the whole point: the rows and the SQL are the
    answer, and losing the sentence that describes them is a much smaller loss
    than turning a successful query into an error page. The UI has a fallback
    line for exactly this.
    """
    preview = {
        "columns": columns,
        "rows": rows[:ROWS_FOR_SUMMARY],
        "row_count": len(rows),
    }
    try:
        return llm.complete(
            _ANSWER_SYSTEM_PROMPT,
            f"Question: {question}\n\nSQL:\n{sql}\n\nResult: {preview}",
        )
    except llm.LlmError:
        return None


@router.post(
    "",
    summary="Ask a natural-language question about the marts",
    responses={
        422: {"description": "The generated SQL was rejected; body carries the attempt"},
        429: {"description": "The LLM provider's free tier throttled the request"},
        502: {"description": "The LLM provider failed or timed out"},
        503: {"description": "No GROQ_API_KEY configured — see the detail for setup"},
    },
)
def ask(request: ChatRequest, conn: DbConn) -> ChatResponse:
    question = request.question.strip()

    # Checked up front so an unconfigured server answers instantly with
    # instructions instead of building a schema briefing it will never use.
    if not llm.is_configured():
        raise llm.LlmNotConfigured()

    schema = build_schema_context()

    sql = _generate_sql(question, schema)
    try:
        safe_sql = validate(sql)
    except UnsafeSql as first_error:
        sql = _generate_sql(question, schema, previous_error=str(first_error))
        try:
            safe_sql = validate(sql)
        except UnsafeSql as second_error:
            # 422, not 500: the request was well-formed and the server is fine —
            # the model's answer was unusable. The attempt goes in the body
            # because seeing the rejected SQL is how anyone debugs this, and
            # hiding it would leave the UI with nothing to show.
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "The model could not produce a safe SQL query for that "
                        "question, twice. Try rephrasing it."
                    ),
                    "sql": sql,
                    "error": str(second_error),
                },
            ) from second_error

    try:
        columns, rows = _run_read_only(conn, safe_sql)
    except SQLAlchemyError as exc:
        # The guard proves the statement is a safe SELECT; it cannot prove the
        # warehouse will accept it (a hallucinated column name parses fine).
        # 422 again, with the SQL, for the same reason as above.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "The generated SQL was rejected by the warehouse.",
                "sql": safe_sql,
                # The database's own message names the bad column, which is the
                # useful part. Only the *original* driver error, not the
                # SQLAlchemy wrapper's str(), which includes the parameters.
                "error": str(getattr(exc, "orig", exc)).strip(),
            },
        ) from exc

    return ChatResponse(
        question=question,
        sql=safe_sql,
        columns=columns,
        rows=rows,
        row_count=len(rows),
        # Conservative: a result that exactly fills the cap is reported as
        # possibly-truncated, because from here the two are indistinguishable.
        truncated=len(rows) >= MAX_ROWS,
        answer=_summarise(question, safe_sql, columns, rows),
        model=llm.model_name(),
    )
