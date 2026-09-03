"""Response models for /api/ingest.

A third kind of shape alongside schemas.py (mart rows) and schemas_catalog.py
(dbt metadata): this file models *an orchestration run*, which is neither
warehouse data nor pipeline metadata but the state of a job in flight.

Airflow's own JSON is not passed through. It carries two dozen fields per task
instance (pool slots, queue, hostname, try numbers, map indexes) that this
platform has no opinion about, and it belongs to a version of an API we do not
control. Normalising to the four fields the UI actually renders means an Airflow
3 upgrade is a change in `routers/ingest.py`, not a change in every client.
"""

from pydantic import BaseModel, Field

# Airflow's terminal DAG-run states. Anything else ("queued", "running") means
# the caller should poll again — see `IngestRunStatus.is_running`, which is
# computed from this so that no two clients can disagree about when to stop.
TERMINAL_STATES = frozenset({"success", "failed"})


class IngestTable(BaseModel):
    """One ingestable raw table and the header a file for it must have."""

    name: str = Field(examples=["raw_payments"])
    staging_model: str = Field(
        description="The dbt model built downstream of this table after a load",
        examples=["stg_collections__payments"],
    )
    columns: list[str] = Field(
        description="Expected header, in the raw table's own column order. "
        "A file may present them in any order, but the names must match.",
        examples=[["payment_id", "account_id", "payment_date", "amount", "method"]],
    )
    label: str = Field(
        description="Human-readable name for a dropdown", examples=["Payments"]
    )


class IngestAccepted(BaseModel):
    """202 body: the file is staged and Airflow has a run for it.

    Deliberately not a 200 with results. The load takes seconds to tens of
    seconds (a COPY plus a scoped `dbt build`), so the honest answer to the
    upload is "accepted, here is where to watch it" rather than a held-open
    connection.
    """

    dag_run_id: str = Field(examples=["manual__2026-09-03T04:20:00+00:00"])
    dag_id: str = Field(examples=["file_ingest"])
    table: str
    filename: str = Field(
        description="The sanitised name the file was staged under, not the one "
        "the client sent"
    )
    poll: str = Field(
        description="Where to read this run's status",
        examples=["/api/ingest/runs/manual__2026-09-03T04:20:00+00:00"],
    )


class IngestTaskState(BaseModel):
    """One task of the run, as much as a progress panel needs."""

    task_id: str = Field(examples=["copy_into_raw"])
    # Nullable because Airflow reports `null` for a task instance it has created
    # but not yet queued — a real state ("not started"), not missing data.
    state: str | None = Field(examples=["success", "running", "failed", "upstream_failed"])
    duration_seconds: float | None = Field(
        default=None, description="Wall clock, once the task has finished"
    )
    started_at: str | None = None
    ended_at: str | None = None


class IngestRunStatus(BaseModel):
    dag_run_id: str
    state: str = Field(
        description="Airflow's dag-run state: queued, running, success or failed",
        examples=["running"],
    )
    is_running: bool = Field(
        description="False once the run reached a terminal state. Clients poll "
        "while this is true and stop when it flips."
    )
    started_at: str | None = None
    ended_at: str | None = None
    tasks: list[IngestTaskState] = Field(
        description="In execution order: copy_into_raw, then dbt_build_downstream"
    )
