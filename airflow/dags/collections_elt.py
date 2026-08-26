"""Collections ELT — an MWAA-shaped Airflow DAG.

Daily flow:
    ingest pipe-delimited files  ->  COPY INTO raw (Snowflake)
      -> dbt build (staging -> intermediate -> marts, with tests)
      -> unload marts to pipe-delimited extract files

This is written exactly as it would run on **AWS MWAA**: the file lives in the
S3 DAGs bucket (`dags/`), SQL lives under `dags/include/sql/`, and the dbt
project is shipped alongside (see ../README.md). To run it locally, set the
AIRFLOW_DBT_DIR / AIRFLOW_DBT_PROFILES_DIR env vars to your local paths.
"""
from __future__ import annotations

import os

import pendulum
from airflow.decorators import dag
from airflow.operators.bash import BashOperator
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

# Airflow Connection to Snowflake. Create it once (UI: Admin > Connections, or
# on MWAA via an env var / Secrets Manager). Conn type "Snowflake".
SNOWFLAKE_CONN_ID = "snowflake_default"

# On MWAA the dbt project is synced next to the DAGs; override locally.
DBT_DIR = os.environ.get("AIRFLOW_DBT_DIR", "/usr/local/airflow/dbt/anz_banking")
DBT_PROFILES_DIR = os.environ.get(
    "AIRFLOW_DBT_PROFILES_DIR", "/usr/local/airflow/dbt/.dbt"
)

default_args = {
    "retries": 2,
    "retry_delay": pendulum.duration(minutes=5),
}


@dag(
    dag_id="collections_elt",
    schedule="0 6 * * *",  # 6am daily
    start_date=pendulum.datetime(2024, 1, 1, tz="Australia/Sydney"),
    catchup=False,
    default_args=default_args,
    tags=["collections", "dbt", "snowflake"],
    doc_md=__doc__,
)
def collections_elt():
    # 1) INGEST — COPY INTO raw landing tables from the (S3/internal) stage.
    #    In local dev you can `dbt seed` instead of this step.
    ingest_files = SQLExecuteQueryOperator(
        task_id="ingest_files",
        conn_id=SNOWFLAKE_CONN_ID,
        sql="include/sql/copy_into_raw.sql",
        split_statements=True,
        return_last=False,
    )

    # 2) TRANSFORM + TEST — dbt build runs the whole DAG and fails on a bad test.
    dbt_build = BashOperator(
        task_id="dbt_build",
        bash_command=(
            f"cd {DBT_DIR} && "
            f"dbt build --profiles-dir {DBT_PROFILES_DIR} --target prod"
        ),
    )

    # 3) GENERATE — unload the marts to outbound pipe-delimited extract files.
    unload_extracts = SQLExecuteQueryOperator(
        task_id="unload_extracts",
        conn_id=SNOWFLAKE_CONN_ID,
        sql="include/sql/unload_marts.sql",
        split_statements=True,
        return_last=False,
    )

    ingest_files >> dbt_build >> unload_extracts


collections_elt()
