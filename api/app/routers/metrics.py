"""KPI endpoints.

Queries are plain parameterised SQL via `text()`. The marts are dbt's contract:
their columns, grain and KPI formulas are defined and tested in the dbt project,
so mapping them to ORM classes here would duplicate that contract in a second
place that can silently drift. Reading the columns dbt promises keeps one source
of truth.
"""

from fastapi import APIRouter
from sqlalchemy import text

from ..db import MARTS, DbConn
from ..schemas import MetricsSummary, PerformanceRow

router = APIRouter(prefix="/api/metrics", tags=["metrics"])

# One statement, one pass over the fact table. The rate formulas deliberately
# mirror models/marts/collections_performance.sql so the portfolio-wide number
# and the per-team breakdown are the same metric at different grains:
#   cure rate     = cured cases / all cases
#   PTP kept rate = kept promises / promises made
#   RPC rate      = right-party contacts / contact attempts
# nullif() guards the division; round(..., 1) matches the mart's precision.
_SUMMARY_SQL = text(f"""
    select
        count(*)                                     as total_cases,
        count(*) filter (where case_status = 'open')  as open_cases,
        coalesce(sum(delinquent_amount), 0)          as total_delinquent_amount,
        round(sum(is_cured) * 100.0
              / nullif(count(*), 0), 1)              as cure_rate_pct,
        round(sum(ptp_kept_count) * 100.0
              / nullif(sum(ptp_count), 0), 1)        as ptp_kept_rate_pct,
        round(sum(rpc_count) * 100.0
              / nullif(sum(contact_attempts), 0), 1) as rpc_rate_pct
    from {MARTS}.fct_collection_cases
""")

_PERFORMANCE_SQL = text(f"""
    select
        team,
        delinquency_bucket,
        case_count,
        delinquent_amount,
        cured_cases,
        written_off_cases,
        cure_rate_pct,
        ptp_kept_rate_pct,
        rpc_rate_pct
    from {MARTS}.collections_performance
    order by team, delinquency_bucket
""")


@router.get("/summary", summary="Portfolio-wide collections KPIs")
def get_summary(conn: DbConn) -> MetricsSummary:
    # An aggregate with no GROUP BY always returns exactly one row.
    row = conn.execute(_SUMMARY_SQL).mappings().one()
    return MetricsSummary(**row)


@router.get("/performance", summary="KPIs by team and delinquency bucket")
def get_performance(conn: DbConn) -> list[PerformanceRow]:
    rows = conn.execute(_PERFORMANCE_SQL).mappings().all()
    return [PerformanceRow(**row) for row in rows]
