"""File-upload ingestion: validate, stage, hand off to Airflow, report progress.

This is the platform's only *write* path into the warehouse, and the shape of it
is the point:

    POST /api/ingest  (multipart)
        │
        ├─ is this table ingestable?          -> 422
        ├─ is the extension one we parse?     -> 422
        ├─ is it under 5 MB?                  -> 413
        ├─ does the header match the table?   -> 422, naming the columns
        │
        ├─ write the bytes into the shared `uploads` volume
        └─ POST a dag run to Airflow          -> 202 {dag_run_id, poll}
                                              -> 503 if Airflow is not running

    GET  /api/ingest/runs/{dag_run_id}        -> normalised run + task states

**The API does not load anything.** It never opens a database transaction here.
Airflow's `file_ingest` DAG does the COPY and runs the scoped `dbt build`, which
is what makes the pipeline observable (retries, logs, a run history) instead of
a request that either worked or didn't. This module is a validating front door.

**Why validate here at all, then?** Because a 40 ms 422 that names the three
columns you got wrong is a better experience than a DAG run that goes red 20
seconds later and hides the reason in a task log. The DAG revalidates
everything, and does so against `information_schema` rather than the table below
— it is the execution authority and cannot delegate that. See TABLE_TO_STAGING
in airflow/dags/file_ingest.py, which is the other half of this deliberate
duplication.
"""

from __future__ import annotations

import csv
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import NamedTuple

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from .. import airflow_client
from ..config import get_settings
from ..schemas_ingest import (
    TERMINAL_STATES,
    IngestAccepted,
    IngestRunStatus,
    IngestTable,
    IngestTaskState,
)

router = APIRouter(prefix="/api/ingest", tags=["ingest"])

# --- what may be uploaded -----------------------------------------------------
#
# The API's mirror of the DAG's TABLE_TO_STAGING, plus the two things the DAG has
# no use for: a label for the dropdown, and the expected header so the browser
# can be told what a valid file looks like *before* anyone uploads one.
#
# Columns are the seed files' headers (anz_banking/seeds/*.csv), which is also
# what the raw tables have, because `dbt seed` created them from those files. The
# DAG checks against the live `information_schema` instead — so if a seed gains a
# column and this table is not updated, the API's pre-flight check gets stricter
# than reality and the fix is a one-line edit here. That failure direction is the
# deliberate one: a stale allow-list rejects good files, it never accepts bad
# ones into the warehouse.
class IngestSpec(NamedTuple):
    """One allow-listed table. A NamedTuple so `spec.columns` type-checks."""

    label: str
    staging_model: str
    columns: tuple[str, ...]


INGESTABLE: dict[str, IngestSpec] = {
    "raw_payments": IngestSpec(
        label="Payments",
        staging_model="stg_collections__payments",
        columns=("payment_id", "account_id", "payment_date", "amount", "method"),
    ),
    "raw_accounts": IngestSpec(
        label="Accounts",
        staging_model="stg_collections__accounts",
        columns=(
            "account_id",
            "customer_id",
            "product_type",
            "open_date",
            "credit_limit",
            "current_balance",
            "status",
        ),
    ),
    "raw_collection_cases": IngestSpec(
        label="Collection cases",
        staging_model="stg_collections__cases",
        columns=(
            "case_id",
            "account_id",
            "agent_id",
            "opened_date",
            "days_past_due",
            "delinquent_amount",
            "status",
            "resolved_date",
        ),
    ),
}

# The DAG's task order, for display. Mirrors the `>>` in file_ingest.py; anything
# unexpected sorts to the end rather than disappearing from the panel.
TASK_ORDER = {"copy_into_raw": 0, "dbt_build_downstream": 1}

# Extension -> delimiter. The default delimiter is inferred from the extension
# because that is what the two formats in this repo actually mean: `.csv` from a
# spreadsheet, `.psv`/`.txt` from the pipe-delimited extracts the Snowflake side
# unloads (see snowflake/). `.txt` is included because that is what mainframe
# extracts are usually named, and it is where the explicit `delimiter` override
# earns its place.
EXTENSION_DELIMITERS = {".csv": ",", ".psv": "|", ".txt": "|"}

# Must agree with ALLOWED_DELIMITERS in the DAG: these two characters end up
# inside a COPY statement, so the set is closed rather than validated.
ALLOWED_DELIMITERS = {",", "|"}

# Everything a filename may keep. Not a blocklist of bad characters — an
# allow-list of good ones, because the list of ways to write "../" is longer than
# the list of characters a demo CSV needs.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename(client_name: str | None) -> str:
    """Reduce a client-supplied filename to something safe to join to a path.

    The client filename is *data*, and it arrives in a multipart header that
    anyone can write by hand. Three things go wrong with it: path traversal
    (`../../etc/passwd`), Windows separators that `Path.name` does not split on,
    and names that are empty or all-dots once cleaned.

    So: take the last segment under either separator, keep only safe characters,
    and fall back to a fixed name if nothing usable is left. The result is only
    ever *part* of the staged name — `_stage()` prefixes a run id, so two uploads
    of the same file cannot collide either.
    """
    raw = (client_name or "").replace("\\", "/")
    base = Path(raw).name
    cleaned = _UNSAFE_CHARS.sub("_", base).strip("._")
    # 80 chars is plenty for a name that already has a unique prefix, and it
    # keeps the full path well under any filesystem's limit.
    return (cleaned[:80] or "upload").lower()


def _delimiter_for(filename: str, override: str | None) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in EXTENSION_DELIMITERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"'{suffix or filename}' is not a supported file type. Upload a "
                f"{', '.join(sorted(EXTENSION_DELIMITERS))} file."
            ),
        )
    if override is None or override == "":
        return EXTENSION_DELIMITERS[suffix]
    if override not in ALLOWED_DELIMITERS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"delimiter must be ',' or '|' (got {override!r}).",
        )
    return override


def _read_capped(upload: UploadFile, limit: int) -> bytes:
    """Read the upload, refusing anything over `limit` without buffering it all.

    Chunked so that the answer to a 50 MB file is a 413 after 5 MB, not after
    50 MB. Starlette has already spooled the body to a temp file by this point,
    so this bounds *our* memory rather than the request's — which is the part
    this code is responsible for.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := upload.file.read(64 * 1024):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"File is larger than the {limit // (1024 * 1024)} MB upload "
                    "limit. This endpoint is for demo extracts; a real landing "
                    "zone would take the file in object storage instead."
                ),
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _check_header(content: bytes, delimiter: str, table: str) -> None:
    """Order-insensitive header check against the expected columns.

    Order-insensitive because a shuffled header is still a valid file — the DAG
    names the columns explicitly in its COPY statement, in the file's own order.
    Case-insensitive for the same reason: Postgres folded these names to
    lowercase when dbt created the table, so a file exported with `Payment_ID`
    describes the same column.
    """
    # utf-8-sig strips the byte-order mark Excel writes, which would otherwise
    # make the first column name unequal to itself on screen.
    first_line = content.split(b"\n", 1)[0].decode("utf-8-sig", errors="replace")
    header = [
        name.strip().lower()
        for name in next(csv.reader([first_line], delimiter=delimiter), [])
    ]
    expected = set(INGESTABLE[table].columns)

    missing = sorted(expected - set(header))
    unexpected = sorted(set(header) - expected)
    if not missing and not unexpected:
        return

    # A structured detail, like /api/chat's 422: the UI shows the diff, and the
    # `message` field keeps generic error rendering readable.
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail={
            "message": (
                f"The file's header does not match {table}. "
                + (f"Missing: {', '.join(missing)}. " if missing else "")
                + (f"Unexpected: {', '.join(unexpected)}." if unexpected else "")
            ),
            "expected": sorted(expected),
            "found": header,
            "missing": missing,
            "unexpected": unexpected,
        },
    )


def _stage(content: bytes, filename: str, run_id: str) -> Path:
    """Write the bytes where Airflow can read them, under a collision-proof name.

    The run id goes in the filename, so the staged file is traceable back to the
    DAG run that consumed it — and two people uploading `payments.csv` at the
    same moment cannot overwrite each other. Nothing deletes these: a landing
    zone you can re-read is how you replay a load, and a demo volume is not where
    disk pressure comes from. A real one would age them out to object storage.
    """
    uploads = get_settings().uploads_dir
    uploads.mkdir(parents=True, exist_ok=True)
    path = uploads / f"{run_id}__{filename}"
    path.write_bytes(content)
    return path


@router.get("/tables", summary="Which raw tables accept an upload, and their headers")
def list_tables() -> list[IngestTable]:
    """Needs no Airflow and no warehouse — it is the allow-list above.

    That matters for the page: the table picker and the "your file must look like
    this" hint render even with the pipeline profile off, so the Ingest page can
    explain itself before it can do anything.
    """
    return [
        IngestTable(
            name=name,
            label=spec.label,
            staging_model=spec.staging_model,
            columns=list(spec.columns),
        )
        for name, spec in INGESTABLE.items()
    ]


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Upload a CSV / pipe-delimited file and start an ingestion run",
    responses={
        413: {"description": "Over the 5 MB upload limit"},
        422: {
            "description": (
                "Unknown table, unsupported extension, or a header that does not "
                "match the table — the body carries the column diff"
            )
        },
        503: {"description": "Airflow is not running — see the detail for the fix"},
    },
)
def upload(
    # `table` is a form field rather than a query parameter so the whole request
    # is one multipart body: the file and the destination arrive together and
    # cannot be separated by a retry or a proxy.
    table: str = Form(description="Raw table to append to; see /api/ingest/tables"),
    file: UploadFile = File(description="A .csv, .psv or .txt file, max 5 MB"),
    delimiter: str | None = Form(
        default=None,
        description="Override the delimiter inferred from the extension (',' or '|')",
    ),
) -> IngestAccepted:
    if table not in INGESTABLE:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=(
                f"'{table}' is not an ingestable table. Allowed: "
                f"{', '.join(sorted(INGESTABLE))}."
            ),
        )

    settings = get_settings()
    # Sanitised first, then used for the extension check too — so a filename
    # crafted to look like `x.csv/../../y` cannot smuggle a delimiter decision
    # past a name the rest of this function no longer uses.
    filename = _safe_filename(file.filename)
    resolved_delimiter = _delimiter_for(filename, delimiter)

    content = _read_capped(file, settings.max_upload_bytes)
    if not content.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The file is empty.",
        )
    _check_header(content, resolved_delimiter, table)

    # One id, used for two things: the DAG run's name in Airflow and the prefix
    # of the staged filename. That is what makes a file in the uploads volume
    # traceable to the run that consumed it, and vice versa. Timestamp for
    # legibility, 8 random hex for uniqueness — two uploads in the same second
    # must not collide. See trigger_file_ingest for why we name the run at all.
    run_id = f"ingest__{datetime.now(UTC):%Y%m%dT%H%M%S}__{uuid.uuid4().hex[:8]}"
    staged = _stage(content, filename, run_id)

    # Triggered last, after the file is on disk: a run that starts before its
    # input exists is a race the DAG would lose. If this raises (Airflow down),
    # the staged file is simply never consumed — which is the harmless half of
    # the two possible orderings.
    run = airflow_client.trigger_file_ingest(
        {
            "table": table,
            # An absolute path inside the *shared volume*, which is mounted at a
            # different path in each container (/srv/uploads here,
            # /opt/airflow/uploads there). Sending only the basename and letting
            # the DAG join it to its own UPLOADS_DIR would be tidier, but the
            # DAG's job is to distrust this value and it already checks the
            # resolved path is inside its uploads directory — which catches a
            # mismatched mount with a clear message instead of a silent miss.
            "file_path": f"/opt/airflow/uploads/{staged.name}",
            "delimiter": resolved_delimiter,
        },
        dag_run_id=run_id,
    )
    dag_run_id = run.get("dag_run_id", run_id)
    return IngestAccepted(
        dag_run_id=dag_run_id,
        dag_id=airflow_client.DAG_ID,
        table=table,
        filename=staged.name,
        poll=f"/api/ingest/runs/{dag_run_id}",
    )


@router.get(
    "/runs/{dag_run_id}",
    summary="State of one ingestion run, task by task",
    responses={
        404: {"description": "Airflow has no run with that id"},
        503: {"description": "Airflow is not running"},
    },
)
def get_run(dag_run_id: str) -> IngestRunStatus:
    """Two Airflow calls, flattened into one small object.

    The dag run gives the overall verdict; the task instances give the two rows
    the progress panel draws. They are fetched together because a panel showing
    "running" with no task detail is not worth a request.
    """
    run = airflow_client.get_dag_run(dag_run_id)
    instances = airflow_client.get_task_instances(dag_run_id)

    tasks = [
        IngestTaskState(
            task_id=instance["task_id"],
            state=instance.get("state"),
            duration_seconds=instance.get("duration"),
            started_at=instance.get("start_date"),
            ended_at=instance.get("end_date"),
        )
        # Airflow returns task instances in no guaranteed order; sorting by
        # start_date would put a not-yet-started task first (null). The DAG's
        # shape is fixed and known, so order by TASK_ORDER instead.
        for instance in sorted(
            instances, key=lambda ti: TASK_ORDER.get(ti.get("task_id", ""), 99)
        )
    ]
    state = run.get("state") or "queued"
    return IngestRunStatus(
        dag_run_id=run.get("dag_run_id", dag_run_id),
        state=state,
        is_running=state not in TERMINAL_STATES,
        started_at=run.get("start_date"),
        ended_at=run.get("end_date"),
        tasks=tasks,
    )
