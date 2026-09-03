"""File-upload ingestion — the DAG behind the dashboard's **Ingest** page.

    browser (drag a CSV onto /ingest)
        │  multipart POST
        ▼
    FastAPI  /api/ingest        writes the file into the shared `uploads` volume,
        │                       then triggers THIS DAG over the Airflow REST API
        │  POST /api/v1/dags/file_ingest/dagRuns
        │  conf = {table, file_path, delimiter}
        ▼
    copy_into_raw               COPY FROM STDIN -> analytics_raw.<table>
        │
        ▼
    dbt_build_downstream        dbt build --select <staging model>+   (tests included)

`schedule=None`: this DAG has no clock. It runs when something uploads a file,
which is the shape most ingestion DAGs actually have once a landing-zone
notification or an API call is what starts them.

**Why the second task is scoped.** `dbt build` with no selector would rebuild the
whole project (~60 nodes) for five new payment rows. `--select <model>+` runs the
touched staging model and only what depends on it, which makes the run fast *and*
makes `run_results.json` a record of exactly this ingestion — which is what the
dashboard's Runs page then shows.

**The failing re-upload is a feature.** Upload the same file twice and the
`unique` test on the staging model's primary key fails, `dbt build` exits
non-zero, and the task goes red. That is the quality gate doing its job: bad data
reached the raw landing table (as it always can) and was stopped before it
reached a mart anyone reads.
"""

from __future__ import annotations

import csv
import os
from contextlib import closing
from pathlib import Path

import pendulum
import psycopg2
from airflow.decorators import dag
from airflow.exceptions import AirflowFailException
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

# --- what may be loaded, and what it feeds ------------------------------------
# An explicit allow-list, not a lookup: `conf` arrives from an HTTP request, and
# "which tables can a stranger append to" is a decision that belongs in code
# under review rather than in whatever string the caller sent. It doubles as the
# routing table — the value is the dbt model to build downstream from.
#
# The API mirrors these keys for its own validation (api/app/routers/ingest.py).
# That duplication is deliberate and noted at both ends: the API validates early
# so the browser gets a fast, specific error, and this DAG revalidates because it
# is the thing that actually writes to the warehouse. An execution layer that
# trusts its caller is an execution layer with no authority.
TABLE_TO_STAGING = {
    "raw_payments": "stg_collections__payments",
    "raw_accounts": "stg_collections__accounts",
    "raw_collection_cases": "stg_collections__cases",
}

# `dbt seed` lands the raw CSVs in <base schema>_raw (see .dbt/profiles.yml), so
# the appended rows have to go to the same place the `collections` source points.
RAW_SCHEMA = os.environ.get("RAW_SCHEMA", "analytics_raw")

# Two characters, because the platform ingests two formats: comma-separated CSV
# and the pipe-delimited extracts the mainframe/Snowflake side of this repo
# produces. Interpolated into a COPY statement below, hence a closed set rather
# than a validated string.
ALLOWED_DELIMITERS = {",", "|"}

# The staging area. A named docker volume shared with the API container: the API
# writes the upload, this DAG reads it. Nothing is passed through `conf` except
# the path, so a 5 MB file never travels through Airflow's metadata database.
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "/opt/airflow/uploads"))

# Same env vars the other DAG uses, so both point at the same checkout.
DBT_DIR = os.environ.get("AIRFLOW_DBT_DIR", "/opt/airflow/dbt/anz_banking")
DBT_PROFILES_DIR = os.environ.get("AIRFLOW_DBT_PROFILES_DIR", "/opt/airflow/dbt/.dbt")


def _connect():
    """Connect to the warehouse from the same POSTGRES_* vars dbt's `local` target reads.

    One set of environment variables for the whole stack (see docker-compose.yml)
    means the COPY below and the `dbt build` after it cannot end up pointed at
    different databases.

    A raw psycopg2 connection rather than Airflow's PostgresHook on purpose:
    a Hook needs an Airflow Connection to exist, which is one more piece of
    demo setup to explain, and `copy_expert` is a psycopg2 API anyway.
    """
    return psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "postgres"),
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        dbname=os.environ.get("POSTGRES_DB", "platform"),
        user=os.environ.get("POSTGRES_USER", "platform"),
        password=os.environ.get("POSTGRES_PASSWORD", "platform"),
    )


def _staged_file(file_path: str) -> Path:
    """Resolve `conf.file_path` and refuse anything outside the uploads volume.

    `../../etc/passwd` is a perfectly valid string to put in a DAG run conf. The
    resolved path has to sit under UPLOADS_DIR, so the only files this DAG can
    ever read are ones something deliberately staged for it.
    """
    resolved = Path(file_path).resolve()
    uploads = UPLOADS_DIR.resolve()
    if not resolved.is_relative_to(uploads):
        raise AirflowFailException(
            f"file_path must be inside {uploads} (got {resolved}). "
            "The API stages uploads there; nothing else is readable."
        )
    if not resolved.is_file():
        raise AirflowFailException(
            f"No staged file at {resolved}. The uploads volume is shared with the "
            "API container — is it mounted in both?"
        )
    return resolved


def _live_columns(cursor, table: str) -> list[str]:
    """The raw table's columns, as the warehouse currently has them.

    The one place this pipeline reads `information_schema`. It is worth it: the
    header check below is only meaningful against the *live* table, and the
    alternative — a hardcoded column list in the DAG — is a second source of
    truth that goes stale the first time a seed gains a column.
    """
    cursor.execute(
        """
        select column_name
        from information_schema.columns
        where table_schema = %s and table_name = %s
        order by ordinal_position
        """,
        (RAW_SCHEMA, table),
    )
    return [row[0] for row in cursor.fetchall()]


def _read_header(path: Path, delimiter: str) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        # utf-8-sig above strips the BOM Excel writes, which would otherwise
        # turn the first column name into "﻿payment_id" and fail the check
        # below with an error nobody can read.
        header = next(csv.reader(handle, delimiter=delimiter), None)
    if not header:
        raise AirflowFailException(f"{path.name} is empty — no header row to check.")
    return [name.strip().lower() for name in header]


def copy_into_raw(**context) -> int:
    """Append a staged file into its raw landing table with COPY FROM STDIN.

    This is Postgres' equivalent of Snowflake's `COPY INTO <table> FROM @stage`
    — the same operation the cloud path in `snowflake/` and the `collections_elt`
    DAG perform, expressed in the local warehouse's dialect. Set-based bulk load
    straight into the table, no row-by-row INSERTs, no pandas in the middle.

    APPEND, never replace: a raw landing table accumulates what arrived. Removing
    yesterday's rows because today's file does not contain them would make the
    warehouse a mirror of the last file rather than a history of every load.
    """
    conf = (context["dag_run"].conf or {})
    table = conf.get("table")
    delimiter = conf.get("delimiter", ",")

    if table not in TABLE_TO_STAGING:
        raise AirflowFailException(
            f"table {table!r} is not ingestable. Allowed: "
            f"{', '.join(sorted(TABLE_TO_STAGING))}."
        )
    if delimiter not in ALLOWED_DELIMITERS:
        raise AirflowFailException(
            f"delimiter {delimiter!r} is not supported (use ',' or '|')."
        )

    path = _staged_file(conf.get("file_path", ""))

    # Handed to the BashOperator downstream via XCom rather than looked up there
    # a second time: the selector that gets built is then, by construction, the
    # one belonging to the table this task validated and wrote to.
    context["ti"].xcom_push(key="staging_model", value=TABLE_TO_STAGING[table])

    # Two nested context managers, two different jobs: `closing` returns the
    # socket, and psycopg2's own connection context manager is the transaction —
    # it commits on a clean exit and rolls back on an exception, so a header
    # mismatch or a bad row leaves the landing table exactly as it was.
    with closing(_connect()) as connection, connection, connection.cursor() as cursor:
        live = _live_columns(cursor, table)
        if not live:
            raise AirflowFailException(
                f"{RAW_SCHEMA}.{table} does not exist. Run `make local-build` once "
                "to create the raw tables before uploading into them."
            )

        header = _read_header(path, delimiter)
        expected = {name.lower() for name in live}
        missing = sorted(expected - set(header))
        unexpected = sorted(set(header) - expected)
        if missing or unexpected:
            # Order-insensitive: a file whose columns are shuffled is still a
            # valid file, and COPY is told the order explicitly below. Names are
            # what must match, and the message says exactly which ones did not —
            # "header mismatch" alone sends people reading CSVs by hand.
            raise AirflowFailException(
                f"{path.name} header does not match {RAW_SCHEMA}.{table}. "
                f"Missing: {missing or 'none'}. Unexpected: {unexpected or 'none'}. "
                f"Expected columns (any order): {sorted(expected)}."
            )

        # The column list comes from the file's own header, so a shuffled file
        # loads correctly. Both halves of this statement are safe to interpolate:
        # `table` was matched against TABLE_TO_STAGING, every column name was
        # matched against information_schema, and the delimiter against
        # ALLOWED_DELIMITERS. Nothing here is a caller-supplied string.
        columns = ", ".join(f'"{name}"' for name in header)
        copy_sql = (
            f"copy {RAW_SCHEMA}.{table} ({columns}) from stdin "
            f"with (format csv, header true, delimiter '{delimiter}')"
        )
        with path.open(newline="", encoding="utf-8-sig") as handle:
            cursor.copy_expert(copy_sql, handle)
        appended = cursor.rowcount

    print(f"COPY appended {appended} rows into {RAW_SCHEMA}.{table} from {path.name}")
    return appended


default_args = {
    # No retries. A header mismatch or a duplicate key fails identically on the
    # second attempt, and the run is in front of a person watching a progress
    # panel — three minutes of retries would just delay the error they need.
    # `collections_elt` (the scheduled DAG) does retry; an interactive one should
    # not.
    "retries": 0,
}


@dag(
    dag_id="file_ingest",
    schedule=None,
    start_date=pendulum.datetime(2024, 1, 1, tz="UTC"),
    catchup=False,
    default_args=default_args,
    tags=["collections", "dbt", "ingestion"],
    doc_md=__doc__,
    params={
        # Documentation, and a usable "Trigger DAG w/ config" form in the Airflow
        # UI. The task revalidates every one of these itself — params are a
        # default, not a guarantee, because a REST caller can post any conf.
        "table": "raw_payments",
        "file_path": "/opt/airflow/uploads/new_payments.csv",
        "delimiter": ",",
    },
)
def file_ingest():
    copy_task = PythonOperator(
        task_id="copy_into_raw",
        python_callable=copy_into_raw,
    )

    # BashOperator rather than a dbt Python API: `dbt build` is a CLI-first tool,
    # and shelling out is exactly what MWAA does in `collections_elt`. The
    # production upgrade is astronomer-cosmos, which turns each dbt model into
    # its own Airflow task — see airflow/README.md.
    #
    # dbt writes its artifacts to <project>/target, which docker-compose.yml
    # bind-mounts from the host and the API container reads (read-only) for the
    # Catalog/Lineage/Runs pages. So this command is what makes the new run show
    # up on the dashboard's Runs page — no extra plumbing, one shared directory.
    dbt_task = BashOperator(
        task_id="dbt_build_downstream",
        bash_command=(
            "dbt build"
            ' --select "{{ ti.xcom_pull(task_ids="copy_into_raw", key="staging_model") }}+"'
            f" --target local --project-dir {DBT_DIR} --profiles-dir {DBT_PROFILES_DIR}"
        ),
    )

    copy_task >> dbt_task


file_ingest()
