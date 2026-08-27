"""Case-level endpoints over fct_collection_cases.

Same reasoning as metrics.py: parameterised `text()` SQL, no ORM models, because
the mart's shape is dbt's contract.
"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import text

from ..db import MARTS, DbConn
from ..schemas import Case

router = APIRouter(prefix="/api/cases", tags=["cases"])

_COLUMNS = """
    case_id, account_id, customer_id, customer_name, product_type, agent_id,
    opened_date, resolved_date, days_past_due, delinquency_bucket,
    delinquent_amount, case_status, is_cured, is_written_off,
    contact_attempts, rpc_count, ptp_count, ptp_kept_count
"""

# The optional filters are expressed as `:param is null or column = :param`
# rather than by concatenating WHERE fragments in Python: one static statement,
# every value bound, nothing to get wrong when a new filter is added. The
# ::text casts tell Postgres the parameter type when the value is NULL.
#
# case_id is the ORDER BY tiebreaker — opened_date alone is not unique, and
# without a stable total order LIMIT/OFFSET paging can repeat or skip rows.
_LIST_SQL = text(f"""
    select {_COLUMNS}
    from {MARTS}.fct_collection_cases
    where (cast(:status as text) is null or case_status = :status)
      and (cast(:bucket as text) is null or delinquency_bucket = :bucket)
    order by opened_date desc, case_id
    limit :limit offset :offset
""")

_GET_SQL = text(f"""
    select {_COLUMNS}
    from {MARTS}.fct_collection_cases
    where case_id = :case_id
""")


@router.get("", summary="List collection cases, newest first")
def list_cases(
    conn: DbConn,
    case_status: str | None = Query(
        default=None,
        # The query param is `status` (what a client would expect); the Python
        # name avoids shadowing fastapi's `status` module imported above.
        alias="status",
        description="Filter by case_status: open | resolved | written_off",
    ),
    bucket: str | None = Query(
        default=None,
        description="Filter by delinquency_bucket, e.g. '1-30 dpd', '90+ dpd'",
    ),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> list[Case]:
    rows = (
        conn.execute(
            _LIST_SQL,
            {"status": case_status, "bucket": bucket, "limit": limit, "offset": offset},
        )
        .mappings()
        .all()
    )
    return [Case(**row) for row in rows]


@router.get(
    "/{case_id}",
    summary="Fetch a single collection case",
    responses={404: {"description": "No case with that id"}},
)
def get_case(case_id: int, conn: DbConn) -> Case:
    row = conn.execute(_GET_SQL, {"case_id": case_id}).mappings().first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"case {case_id} not found",
        )
    return Case(**row)
