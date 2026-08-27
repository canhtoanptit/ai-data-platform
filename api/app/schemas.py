"""Response models.

Money and rates are typed `float` rather than `Decimal`. Pydantic serialises
Decimal to a JSON *string* to preserve precision, which every JS client then has
to parse; these are display figures for a dashboard, so float is the friendlier
contract. Anything doing real money arithmetic should keep the Decimal.
"""

from datetime import date

from pydantic import BaseModel, Field


class Health(BaseModel):
    status: str = Field(examples=["ok"])
    database: str = Field(description="'ok' or 'fail'", examples=["ok"])
    detail: str | None = Field(default=None, description="Error text when the db check failed")


class MetricsSummary(BaseModel):
    """Portfolio-wide rollup of fct_collection_cases."""

    total_cases: int
    open_cases: int
    total_delinquent_amount: float
    # Rates are None, not 0, when the denominator is empty (no cases at all, or
    # no promises to pay) — "unknown" and "zero percent" are different answers.
    cure_rate_pct: float | None
    ptp_kept_rate_pct: float | None
    rpc_rate_pct: float | None


class PerformanceRow(BaseModel):
    """One row of the collections_performance mart (team x delinquency bucket)."""

    team: str | None
    delinquency_bucket: str
    case_count: int
    delinquent_amount: float
    cured_cases: int
    written_off_cases: int
    cure_rate_pct: float | None
    ptp_kept_rate_pct: float | None
    rpc_rate_pct: float | None


class Case(BaseModel):
    """One row of fct_collection_cases."""

    case_id: int
    account_id: int
    customer_id: int
    customer_name: str | None
    product_type: str | None
    agent_id: int | None
    opened_date: date
    resolved_date: date | None
    days_past_due: int
    delinquency_bucket: str
    delinquent_amount: float
    case_status: str = Field(examples=["open", "resolved", "written_off"])
    is_cured: int
    is_written_off: int
    contact_attempts: int
    rpc_count: int
    ptp_count: int
    ptp_kept_count: int
