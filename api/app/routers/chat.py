"""Ask the marts a question in English: NL -> SQL -> rows -> prose.

The pipeline itself lives in [`app/nl2sql.py`](../nl2sql.py) — prompts, the LLM
calls, the guard, the read-only execution. It was extracted from this file so
that the eval harness (`evals/run.py`) exercises the identical code path; a
runner with its own prompt measures a system nobody ships.

What is left here is everything HTTP:

    request validation (pydantic)
        │
        ▼
    is there a key?            ── no ──► 503 with setup instructions (untraced)
        │
        ▼
    rate limit, per client IP  ── over ──► 429 (untraced: slowapi rejects first)
        │
        ▼
    today's token budget       ── spent ──► 429, traced
        │
        ▼
    nl2sql.generate_validated_sql ── guard refused twice ──► 422 + the attempt
        │
        ▼
    nl2sql.execute                ── warehouse refused ────► 422 + the SQL
        │
        ▼
    nl2sql.summarise (best effort) ──► 200
        │
        ▼
    tracing.record  (always, in a finally — see app/tracing.py)

**Untrusted SQL is handled in three independent layers**, none of which relies on
the others being correct (they are implemented in nl2sql.py / sql_guard.py):

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

import time

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy.exc import SQLAlchemyError

from .. import llm, nl2sql, tracing
from ..config import get_settings
from ..db import DbConn
from ..dbt_artifacts import ArtifactsUnavailable
from ..rate_limit import limiter
from ..schema_context import build_schema_context
from ..schemas_chat import ChatRequest, ChatResponse

router = APIRouter(prefix="/api/chat", tags=["chat"])

# Read once at import: slowapi wants the limit as a string on the decorator, and
# settings are process-wide anyway. Override with CHAT_RATE_LIMIT.
_RATE_LIMIT = get_settings().chat_rate_limit


def _budget_exhausted_detail(status_: tracing.BudgetStatus) -> str:
    return (
        f"The daily LLM token budget is exhausted "
        f"({status_.used:,}/{status_.budget:,} used); it resets at midnight UTC. "
        "Raise LLM_DAILY_TOKEN_BUDGET if this server should spend more."
    )


@router.post(
    "",
    summary="Ask a natural-language question about the marts",
    responses={
        422: {"description": "The generated SQL was rejected; body carries the attempt"},
        429: {
            "description": (
                "Rate limited (too many questions from this IP), the daily token "
                "budget is spent, or the LLM provider's free tier throttled us"
            )
        },
        502: {"description": "The LLM provider failed or timed out"},
        503: {"description": "No GROQ_API_KEY configured — see the detail for setup"},
    },
)
# slowapi keys on the client IP, and it finds the Request by looking for a
# parameter *named* `request` — hence the raw Request keeps that name and the
# body model is `payload`, even though this handler never reads `request`.
@limiter.limit(_RATE_LIMIT)
def ask(request: Request, payload: ChatRequest, conn: DbConn) -> ChatResponse:
    question = payload.question.strip()

    # Checked up front so an unconfigured server answers instantly with
    # instructions instead of building a schema briefing it will never use. This
    # is also the one path deliberately left *untraced*: there is no call to
    # trace, no model, no tokens — only a server that is not set up yet, which is
    # a fact about configuration rather than an event in the LLM's history.
    if not llm.is_configured():
        raise llm.LlmNotConfigured()

    trace = tracing.LlmCallTrace(question=question, model=llm.model_name(), source="chat")
    started = time.perf_counter()
    try:
        # Budget enforcement is one SQL query over the trace table — the same
        # artifact the observability endpoint reads. Observability and control
        # from one place, so the number you are shown is the number that stops
        # you. Checked before the LLM call, since the point is not to make it.
        budget = tracing.budget_status()
        if budget.exhausted:
            trace.error_class = "BudgetExhausted"
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=_budget_exhausted_detail(budget),
            )

        schema = build_schema_context()
        generation = nl2sql.generate_validated_sql(question, schema)
        trace.tokens_prompt = generation.tokens_prompt
        trace.tokens_completion = generation.tokens_completion
        trace.latency_ms_llm = generation.latency_ms
        trace.sql_text = generation.safe_sql or generation.sql
        trace.guard_ok = generation.valid
        trace.guard_error = generation.guard_error

        if generation.safe_sql is None:
            # 422, not 500: the request was well-formed and the server is fine —
            # the model's answer was unusable. The attempt goes in the body
            # because seeing the rejected SQL is how anyone debugs this, and
            # hiding it would leave the UI with nothing to show.
            trace.error_class = "UnsafeSql"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": (
                        "The model could not produce a safe SQL query for that "
                        "question, twice. Try rephrasing it."
                    ),
                    "sql": generation.sql,
                    "error": generation.guard_error,
                },
            )

        try:
            execution = nl2sql.execute(conn, generation.safe_sql)
        except SQLAlchemyError as exc:
            # The guard proves the statement is a safe SELECT; it cannot prove
            # the warehouse will accept it (a hallucinated column name parses
            # fine). 422 again, with the SQL, for the same reason as above.
            trace.error_class = "WarehouseRejected"
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "message": "The generated SQL was rejected by the warehouse.",
                    "sql": generation.safe_sql,
                    # The database's own message names the bad column, which is
                    # the useful part. Only the *original* driver error, not the
                    # SQLAlchemy wrapper's str(), which includes the parameters.
                    "error": str(getattr(exc, "orig", exc)).strip(),
                },
            ) from exc

        trace.latency_ms_sql = execution.latency_ms
        trace.row_count = execution.row_count

        summary = nl2sql.summarise(question, generation.safe_sql, execution)
        if summary is not None:
            # The summarising call's tokens count too. It is the same key and the
            # same bill, and a budget that only counted the SQL leg would
            # under-report every successful request by a third.
            trace.tokens_prompt = (trace.tokens_prompt or 0) + (summary.tokens_prompt or 0)
            trace.tokens_completion = (trace.tokens_completion or 0) + (
                summary.tokens_completion or 0
            )

        trace.answered = True
        trace.http_status = status.HTTP_200_OK
        return ChatResponse(
            question=question,
            sql=generation.safe_sql,
            columns=execution.columns,
            rows=execution.rows,
            row_count=execution.row_count,
            truncated=execution.truncated,
            answer=summary.text if summary is not None else None,
            model=llm.model_name(),
        )
    except HTTPException as exc:
        trace.http_status = exc.status_code
        # A short label, never the detail body: the detail can carry the model's
        # SQL (already in sql_text) and this column exists to be grouped by. Each
        # raise site above sets its own; this is the fallback for any it misses.
        trace.error_class = trace.error_class or type(exc).__name__
        raise
    except llm.LlmError as exc:
        # 502/429/503 from the provider. The class name *is* the taxonomy here
        # (LlmRateLimited vs LlmUnavailable), so it is exactly what you would
        # group by when asking "why did today go wrong".
        trace.http_status = exc.status_code
        trace.error_class = type(exc).__name__
        raise
    except ArtifactsUnavailable:
        # dbt has not run, so there is no schema briefing to write SQL against.
        # main.py's handler turns this into a 503; recorded here so the trace
        # agrees with what the client was told rather than defaulting to 500.
        trace.http_status = status.HTTP_503_SERVICE_UNAVAILABLE
        trace.error_class = "ArtifactsUnavailable"
        raise
    finally:
        trace.latency_ms_total = round((time.perf_counter() - started) * 1000)
        tracing.record(trace)
