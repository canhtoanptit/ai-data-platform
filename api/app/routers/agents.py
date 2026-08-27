"""Agent dimension endpoint over dim_agents."""

from fastapi import APIRouter
from sqlalchemy import text

from ..db import MARTS, DbConn
from ..schemas import Agent

router = APIRouter(prefix="/api/agents", tags=["agents"])

# agent_id as tiebreaker: team alone is not a total order, and a stable order
# keeps the response deterministic (same reasoning as the cases listing).
_AGENTS_SQL = text(f"""
    select
        agent_id,
        agent_name,
        team,
        hire_date
    from {MARTS}.dim_agents
    order by team, agent_id
""")


@router.get("", summary="List all agents")
def get_agents(conn: DbConn) -> list[Agent]:
    rows = conn.execute(_AGENTS_SQL).mappings().all()
    return [Agent(**row) for row in rows]
