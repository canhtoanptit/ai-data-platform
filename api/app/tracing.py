"""One row per LLM call, in Postgres — and the daily token budget read off it.

**Why a table and not a log line.** An LLM feature has two questions nobody can
answer from prose logs: *what did this cost* and *is it working*. Both are
aggregate questions over past calls, so the answer is a table you can `group by`.
Once it exists, three things fall out of the same artifact for free:

    platform_ops.llm_calls ──► GET /api/observability/llm   (what happened)
                           ──► the daily token budget       (what it may cost)
                           ──► eval reports vs live traffic (source + prompt_version)

The budget in particular is *just a SQL query over this table* — no counter, no
Redis, no in-process state to lose on restart, and it stays correct across
replicas because Postgres is the one place all of them agree.

**Why `platform_ops` and not one of the dbt schemas.** This is operational data
owned by the API: the API creates it, the API writes it, and dbt must never see
it. Putting it in `analytics_*` would make it look like a mart — something with a
declared grain and tests, that `dbt build` may drop and rebuild. A separate
schema keeps the ownership boundary visible in the object name itself.

**Why the DDL lives here.** The table is created on first use with
CREATE ... IF NOT EXISTS rather than by a migration tool. For one operational
table owned by one service, Alembic would be more machinery than the thing it
manages; and the API must come up cleanly on a fresh Postgres with no extra
step. The trade is real (no versioned migration path if the columns change), and
the escape hatch is that dropping the table loses only history.

**Tracing is best-effort, everywhere.** Every write here is wrapped: a broken
trace must never fail the request it is describing. Observability that can take
down the thing it observes is a liability, not a safety net — so failures are
logged and swallowed, and the caller never learns whether the insert landed.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .config import get_settings
from .db import engine

logger = logging.getLogger(__name__)

SCHEMA = "platform_ops"
TABLE = f"{SCHEMA}.llm_calls"

# Kept as one statement list rather than a .sql file so the shape of the table is
# readable next to the code that writes it.
_DDL = (
    f"create schema if not exists {SCHEMA}",
    f"""
    create table if not exists {TABLE} (
        id                serial primary key,
        ts                timestamptz  not null default now(),
        -- 'chat' for a real request, 'eval' for the harness. Same pipeline, very
        -- different traffic: evals are a batch of known questions, and mixing
        -- them into the live numbers would flatter (or ruin) every average.
        source            text         not null default 'chat',
        question          text         not null,
        prompt_version    text         not null,
        model             text         not null,
        tokens_prompt     int,
        tokens_completion int,
        latency_ms_total  int          not null,
        latency_ms_llm    int,
        latency_ms_sql    int,
        sql_text          text,
        guard_ok          boolean,
        guard_error       text,
        row_count         int,
        answered          boolean      not null,
        error_class       text,
        http_status       int          not null
    )
    """,
    # The two access patterns are both "recent rows": today's token sum for the
    # budget, and the last 20 for the observability panel.
    f"create index if not exists llm_calls_ts_idx on {TABLE} (ts desc)",
)

# Midnight UTC of the current day, as a timestamptz. UTC and not the server's
# local zone so the reset time is the same fact everywhere, and so the message in
# the 429 ("resets at midnight UTC") is literally true.
TODAY_START = "date_trunc('day', now() at time zone 'utc') at time zone 'utc'"

_INSERT = f"""
insert into {TABLE} (
    source, question, prompt_version, model,
    tokens_prompt, tokens_completion,
    latency_ms_total, latency_ms_llm, latency_ms_sql,
    sql_text, guard_ok, guard_error, row_count,
    answered, error_class, http_status
) values (
    :source, :question, :prompt_version, :model,
    :tokens_prompt, :tokens_completion,
    :latency_ms_total, :latency_ms_llm, :latency_ms_sql,
    :sql_text, :guard_ok, :guard_error, :row_count,
    :answered, :error_class, :http_status
)
"""

# Module-level latch, not a per-call CREATE: the DDL is idempotent but it is
# still a round trip, and every request would pay for it. Reset on failure so a
# database that was down when the first request arrived gets another chance.
_table_ready = False


@dataclass(slots=True)
class LlmCallTrace:
    """The row being assembled, mutated as the request proceeds.

    A mutable dataclass rather than kwargs at the end, because the fields are
    filled in at different points in the handler and several of them (the guard
    result, the row count) are only known if the request got that far. The
    defaults are therefore all "didn't happen", which is what a row for a failed
    request should say.
    """

    question: str
    model: str
    source: Literal["chat", "eval"] = "chat"
    prompt_version: str = ""
    tokens_prompt: int | None = None
    tokens_completion: int | None = None
    latency_ms_total: int = 0
    latency_ms_llm: int | None = None
    latency_ms_sql: int | None = None
    sql_text: str | None = None
    guard_ok: bool | None = None
    guard_error: str | None = None
    row_count: int | None = None
    answered: bool = False
    error_class: str | None = None
    http_status: int = 500
    # Not a column: the eval runner keeps its own per-question timings and this
    # keeps them off the row. Present so `asdict` stays the whole story.
    extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def as_params(self) -> dict[str, Any]:
        params = asdict(self)
        params.pop("extra")
        return params


def ensure_table() -> None:
    """Create the schema, table and index if they are not there yet.

    Idempotent and cheap after the first call (see `_table_ready`). Raises on a
    database failure — callers decide whether that is fatal; for tracing it is
    not.
    """
    global _table_ready
    if _table_ready:
        return
    with engine.begin() as connection:
        for statement in _DDL:
            connection.execute(text(statement))
    _table_ready = True


def record(trace: LlmCallTrace) -> None:
    """Write one row. Never raises.

    Uses its own connection from the pool rather than the request's, for two
    reasons: the request's connection may have just failed mid-transaction (a
    hallucinated column name), and a trace write must not join the transaction
    whose outcome it is recording.
    """
    global _table_ready
    if not trace.prompt_version:
        # Filled here rather than defaulted on the dataclass so the constant has
        # exactly one home (nl2sql.PROMPT_VERSION) and this module does not
        # import the pipeline just to know its version.
        from .nl2sql import PROMPT_VERSION

        trace.prompt_version = PROMPT_VERSION
    try:
        ensure_table()
        with engine.begin() as connection:
            connection.execute(text(_INSERT), trace.as_params())
    except SQLAlchemyError:
        # except-log-continue, on purpose. The request this row describes has
        # already succeeded or failed on its own merits; turning a full trace
        # table or a dropped connection into a 500 would mean the observability
        # layer is the least reliable part of the feature.
        _table_ready = False
        logger.warning("could not write an llm_calls trace row", exc_info=True)


def tokens_used_today() -> int:
    """Today's (UTC) total token spend across every source.

    Evals count. They are real tokens against the same key, and a budget that
    ignored them would be a budget you can walk straight through by running the
    harness in a loop.
    """
    ensure_table()
    with engine.connect() as connection:
        used = connection.execute(
            text(
                f"""
                select coalesce(sum(
                    coalesce(tokens_prompt, 0) + coalesce(tokens_completion, 0)
                ), 0)
                from {TABLE}
                where ts >= {TODAY_START}
                """
            )
        ).scalar()
    return int(used or 0)


def daily_budget() -> int:
    return get_settings().llm_daily_token_budget


@dataclass(frozen=True, slots=True)
class BudgetStatus:
    used: int
    budget: int

    @property
    def exhausted(self) -> bool:
        # >=, not >: once the budget is reached the next call would exceed it,
        # and the cost of a call is not known until after it is made.
        return self.used >= self.budget


def budget_status() -> BudgetStatus:
    """Check the budget, failing *open* if the trace table cannot be read.

    Same reasoning as `record`: the ops table is not on the critical path of
    answering a question. If Postgres is unreachable the request is about to
    fail loudly at the SQL-execution leg anyway, so refusing it here with a
    misleading "budget exhausted" would be a worse answer than letting it
    through.
    """
    budget = daily_budget()
    try:
        return BudgetStatus(used=tokens_used_today(), budget=budget)
    except SQLAlchemyError:
        logger.warning("could not read the token budget; allowing the call", exc_info=True)
        return BudgetStatus(used=0, budget=budget)
