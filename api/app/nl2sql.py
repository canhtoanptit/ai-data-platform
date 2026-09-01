"""The NL->SQL pipeline: prompt, LLM call, guard, execution, summary.

**Why this file exists.** In Phase 3 all of this lived inside `routers/chat.py`
as private helpers of the HTTP handler. The eval harness (`evals/run.py`) needs
to run the *exact same* generation the endpoint runs — a runner with its own
prompt, its own retry rule or its own timeout measures a system nobody ships.
So the pipeline moved here and `routers/chat.py` became what it should always
have been: HTTP concerns only (status codes, budget, tracing, response model).

The split is drawn at the HTTP boundary. This module knows about prompts, tokens
and latencies; it raises no `HTTPException` and returns no response model. Both
callers map its results onto their own output:

    routers/chat.py  ──┐
                       ├──► nl2sql.generate_validated_sql ──► nl2sql.execute
    evals/run.py     ──┘

Everything measurable is measured here, once, in milliseconds — the endpoint
needs the numbers for the trace table and the runner needs them for the report,
and timing the same call twice in two places is how the two drift apart.
"""

from __future__ import annotations

import datetime as dt
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from . import llm
from .sql_guard import MAX_ROWS, UnsafeSql, strip_code_fences, validate

# Bump this whenever the prompts below change in a way that could move the
# model's output — wording, rules, the domain notes it leans on, the retry
# policy. Evals and traces are only comparable *within* a version: an accuracy
# of 3/4 from last week and 4/4 from today mean nothing side by side if the
# prompt changed in between, and the trace table's `prompt_version` column is
# what lets you notice that instead of celebrating.
PROMPT_VERSION = "2026-08-31.1"

# How long a single question's query may run. Small deliberately: every
# reasonable question about an 8-row demo mart answers in milliseconds, so 5s is
# already two orders of magnitude of headroom, and anything slower is a mistake
# worth interrupting rather than waiting for.
STATEMENT_TIMEOUT = "5s"

# Rows sent to the summarising call. Capped well below MAX_ROWS because prose
# over 100 rows is not prose anyone reads, and the tokens are better spent on
# the schema. The table in the UI shows all of them regardless.
ROWS_FOR_SUMMARY = 20

SQL_SYSTEM_PROMPT = f"""\
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

ANSWER_SYSTEM_PROMPT = """\
You are a data analyst reporting a query result to a colleague.

Given a question, the SQL that answered it and the rows it returned, reply with
two or three plain sentences stating what the numbers show. Quote the specific
figures. Do not describe the SQL, do not mention tables or columns, and do not
apologise or add caveats about the data. If the result is empty, say so plainly.
"""


def json_safe(value: Any) -> Any:
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


@dataclass(slots=True)
class Generation:
    """What the SQL-writing leg produced, cost and took.

    `sql` is always the model's last attempt; `safe_sql` is the guard's output
    and is None when the guard refused both attempts. Keeping both means a
    caller can report the rejected text (the endpoint's 422 body, the eval
    report) without a second source of truth for "what ran".
    """

    sql: str
    safe_sql: str | None
    guard_error: str | None
    attempts: int
    tokens_prompt: int | None
    tokens_completion: int | None
    latency_ms: int

    @property
    def valid(self) -> bool:
        return self.safe_sql is not None


@dataclass(slots=True)
class Execution:
    columns: list[str]
    rows: list[list[Any]]
    latency_ms: int

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def truncated(self) -> bool:
        # Conservative: a result that exactly fills the cap is reported as
        # possibly-truncated, because from here the two are indistinguishable.
        return len(self.rows) >= MAX_ROWS


def _millis(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


def _generate_once(
    question: str, schema: str, previous_error: str | None = None
) -> llm.Completion:
    """One SQL-writing call. `previous_error` appends the validator's complaint."""
    prompt = f"Question: {question}"
    if previous_error is not None:
        prompt += (
            f"\n\nYour previous SQL was rejected: {previous_error}\n"
            "Return corrected SQL. SQL only."
        )
    return llm.complete(SQL_SYSTEM_PROMPT.format(schema=schema), prompt)


def generate_validated_sql(question: str, schema: str) -> Generation:
    """Write SQL, validate it, and retry once with the guard's complaint.

    Retrying with the error is the cheapest accuracy fix available: the common
    failures (a hallucinated column, a stray fence, a trailing explanation) are
    ones the model corrects immediately once told. One retry, not a loop —
    beyond the first, failures are usually the question, not the model, and a
    loop would burn a rate-limited free tier on it.

    Raises `llm.LlmError` if the provider itself fails; a guard rejection is a
    *result* (`valid is False`), not an exception, because both callers have
    something to report about it.
    """
    started = time.perf_counter()
    tokens_prompt = 0
    tokens_completion = 0
    previous_error: str | None = None

    # Two passes at most; `attempts` is 1 or 2 so the trace and the report can
    # tell "got it first time" apart from "needed the retry".
    for attempt in (1, 2):
        completion = _generate_once(question, schema, previous_error)
        # Summed across attempts: a retry is not free, and a budget that only
        # counted the successful call would under-report exactly the requests
        # that cost the most.
        tokens_prompt += completion.tokens_prompt or 0
        tokens_completion += completion.tokens_completion or 0
        sql = strip_code_fences(completion.text)
        try:
            safe_sql = validate(sql)
        except UnsafeSql as error:
            previous_error = str(error)
            if attempt == 2:
                return Generation(
                    sql=sql,
                    safe_sql=None,
                    guard_error=previous_error,
                    attempts=attempt,
                    tokens_prompt=tokens_prompt,
                    tokens_completion=tokens_completion,
                    latency_ms=_millis(started),
                )
            continue
        return Generation(
            sql=sql,
            safe_sql=safe_sql,
            guard_error=None,
            attempts=attempt,
            tokens_prompt=tokens_prompt,
            tokens_completion=tokens_completion,
            latency_ms=_millis(started),
        )
    raise AssertionError("unreachable: the loop returns on both attempts")


def execute(conn: Connection, sql: str) -> Execution:
    """Execute validated SQL inside a read-only, time-boxed transaction.

    `SET TRANSACTION READ ONLY` has to be the first thing in the transaction, so
    the explicit `conn.begin()` matters — with SQLAlchemy's autobegin the SELECT
    would open the transaction and the SET would arrive too late to apply to it.
    `SET LOCAL` scopes the timeout to this transaction so it cannot leak onto the
    next request to reuse this pooled connection.
    """
    started = time.perf_counter()
    with conn.begin():
        conn.execute(text("set transaction read only"))
        conn.execute(text(f"set local statement_timeout = '{STATEMENT_TIMEOUT}'"))
        result = conn.execute(text(sql))
        columns = list(result.keys())
        rows = [[json_safe(value) for value in row] for row in result.fetchmany(MAX_ROWS)]
    return Execution(columns=columns, rows=rows, latency_ms=_millis(started))


def summarise(question: str, sql: str, execution: Execution) -> llm.Completion | None:
    """Prose over the rows, or None if the second LLM call fails.

    Swallowing this failure is the whole point: the rows and the SQL are the
    answer, and losing the sentence that describes them is a much smaller loss
    than turning a successful query into an error page. The UI has a fallback
    line for exactly this.
    """
    preview = {
        "columns": execution.columns,
        "rows": execution.rows[:ROWS_FOR_SUMMARY],
        "row_count": execution.row_count,
    }
    try:
        return llm.complete(
            ANSWER_SYSTEM_PROMPT,
            f"Question: {question}\n\nSQL:\n{sql}\n\nResult: {preview}",
        )
    except llm.LlmError:
        return None
