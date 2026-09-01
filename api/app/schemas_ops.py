"""Response models for /api/observability/llm.

A fourth schemas file, and the first one that models the API's *own* operational
data rather than the warehouse's. Everything here is read from
`platform_ops.llm_calls` — see app/tracing.py for why that table exists.
"""

from pydantic import BaseModel, Field


class LlmUsageToday(BaseModel):
    """The budget panel: what today has cost and how much is left.

    `budget_used_pct` is computed server-side rather than left to the client
    because the endpoint already knows both numbers, and two clients dividing
    them differently (or dividing by zero when the budget is 0) is a bug waiting
    to happen twice.
    """

    calls: int
    tokens: int
    budget: int
    budget_used_pct: float = Field(
        description="tokens/budget as a percentage, 0 when the budget is 0"
    )


class LlmCallRow(BaseModel):
    """One traced call, trimmed for a table.

    The question is truncated here, not in CSS: this is an operations view, and
    shipping 500-character questions to render 60 visible characters is waste on
    both sides.
    """

    ts: str = Field(description="ISO 8601 UTC")
    question: str
    source: str = Field(description="'chat' for a real request, 'eval' for the harness")
    model: str
    guard_ok: bool | None
    row_count: int | None
    tokens: int | None = Field(description="prompt + completion, null when unknown")
    latency_ms_total: int
    http_status: int
    error_class: str | None


class LlmObservability(BaseModel):
    today: LlmUsageToday
    recent: list[LlmCallRow] = Field(description="Most recent first")
