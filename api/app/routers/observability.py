"""What the LLM feature did today: GET /api/observability/llm.

Reads `platform_ops.llm_calls` (see app/tracing.py) and answers two questions in
one payload: *what is this costing* (today's calls, tokens, and where that sits
against the daily budget) and *is it working* (the last 20 calls with their guard
result, row count, latency and status).

**Always 200.** An empty table, a table that does not exist yet, an unreachable
database — all of them answer with zeros and an empty list. This is the endpoint
a dashboard polls, and a monitoring panel that turns red because *nothing has
happened yet* trains people to ignore it. "No calls today" is a legitimate
answer, not an error.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from .. import tracing
from ..db import DbConn
from ..schemas_ops import LlmCallRow, LlmObservability, LlmUsageToday

router = APIRouter(prefix="/api/observability", tags=["observability"])

# Enough to see the last few minutes of activity and any run of failures, few
# enough to render as a table without paging. Not configurable: a bigger window
# is a different feature (a searchable trace view), not a query parameter.
RECENT_LIMIT = 20

# Long enough to identify a question, short enough to keep a table row on one
# line at typical widths.
QUESTION_PREVIEW = 90

_TODAY_SQL = f"""
select
    count(*) as calls,
    coalesce(sum(
        coalesce(tokens_prompt, 0) + coalesce(tokens_completion, 0)
    ), 0) as tokens
from {tracing.TABLE}
where ts >= {tracing.TODAY_START}
"""

_RECENT_SQL = f"""
select
    ts, question, source, model, guard_ok, row_count,
    tokens_prompt, tokens_completion, latency_ms_total, http_status, error_class
from {tracing.TABLE}
order by ts desc, id desc
limit {RECENT_LIMIT}
"""

def _truncate(question: str) -> str:
    if len(question) <= QUESTION_PREVIEW:
        return question
    return question[: QUESTION_PREVIEW - 1].rstrip() + "…"


def _tokens(prompt: int | None, completion: int | None) -> int | None:
    """prompt + completion, or None when the provider reported neither.

    Not `(prompt or 0) + (completion or 0)`: that renders an unknown as a
    confident 0, and 0 tokens is a claim about a call that did not happen.
    """
    if prompt is None and completion is None:
        return None
    return (prompt or 0) + (completion or 0)


@router.get(
    "/llm",
    summary="Today's LLM spend against the budget, plus the last 20 calls",
    response_description="Zeros and an empty list when nothing has been traced yet",
)
def llm_observability(conn: DbConn) -> LlmObservability:
    budget = tracing.daily_budget()
    try:
        today = conn.execute(text(_TODAY_SQL)).one()
        recent = conn.execute(text(_RECENT_SQL)).all()
    except SQLAlchemyError:
        # The table is created on first use by the *writer*, so before the first
        # chat request it genuinely is not there. Deliberately not created here:
        # a GET should not run DDL, and "no data yet" is already the right answer.
        # The budget is still reported — it is configuration, not history.
        return LlmObservability(
            today=LlmUsageToday(calls=0, tokens=0, budget=budget, budget_used_pct=0.0),
            recent=[],
        )

    tokens = int(today.tokens)
    return LlmObservability(
        today=LlmUsageToday(
            calls=int(today.calls),
            tokens=tokens,
            budget=budget,
            # Guarded: a budget of 0 is a legal (if odd) configuration meaning
            # "no calls allowed", and it must not divide by zero here.
            budget_used_pct=round(tokens * 100 / budget, 1) if budget else 0.0,
        ),
        recent=[
            LlmCallRow(
                ts=row.ts.isoformat(),
                question=_truncate(row.question),
                source=row.source,
                model=row.model,
                guard_ok=row.guard_ok,
                row_count=row.row_count,
                tokens=_tokens(row.tokens_prompt, row.tokens_completion),
                latency_ms_total=row.latency_ms_total,
                http_status=row.http_status,
                error_class=row.error_class,
            )
            for row in recent
        ],
    )
