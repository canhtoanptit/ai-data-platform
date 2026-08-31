"""Request/response models for /api/chat.

A third schemas file, for the same reason there are already two: schemas.py
models mart *rows*, schemas_catalog.py models *pipeline metadata*, and this one
models an *ad-hoc query result* whose shape is only known at runtime. Hence
`columns` + `rows` rather than typed fields — the whole point of the endpoint is
that the SQL is written per question.
"""

from typing import Any

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    # Bounded at both ends: an empty question wastes an LLM call, and 500
    # characters is far more than a data question needs while capping what a
    # caller can push into the prompt.
    question: str = Field(
        min_length=1,
        max_length=500,
        examples=["Which team has the highest cure rate?"],
    )


class ChatResponse(BaseModel):
    """Everything the UI needs to show its work: the SQL, the rows, the prose.

    The SQL is returned always, not just on success, because "what did it
    actually run?" is the first question anyone asks of a text-to-SQL feature —
    and being able to check it is what makes the answer trustworthy.
    """

    question: str
    sql: str = Field(
        description="The validated, row-limited SQL that was executed",
        examples=["SELECT team, cure_rate_pct FROM analytics_marts.collections_performance"],
    )
    columns: list[str] = Field(description="Result column names, in select order")
    rows: list[list[Any]] = Field(
        description=(
            "Result rows as positional lists matching `columns`. Dates are ISO "
            "strings and numerics are JSON numbers — see _json_safe in the router."
        )
    )
    row_count: int
    truncated: bool = Field(
        description="True when the result hit the 100-row cap, so there may be more"
    )
    answer: str | None = Field(
        default=None,
        description=(
            "Two or three sentences of prose over the rows, or null if the "
            "summarising call failed — the rows are the answer either way"
        ),
    )
    model: str = Field(
        description="The LLM that wrote the SQL", examples=["llama-3.3-70b-versatile"]
    )
