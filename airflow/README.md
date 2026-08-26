# Orchestration — Airflow / AWS MWAA

The `collections_elt` DAG orchestrates the full ELT: **ingest files → dbt build
→ generate extract files**. It's written the way it deploys to **AWS MWAA**, but
you can practise the exact same skill locally for free.

```
dags/
  collections_elt.py           # the DAG (ingest -> dbt build -> unload)
  include/sql/
    copy_into_raw.sql          # COPY INTO raw landing table
    unload_marts.sql           # COPY INTO @stage (file generation)
requirements.txt               # Python deps MWAA installs
```

## Practise locally (free)

Airflow and dbt can fight over dependencies, so use a **separate virtualenv**
for Airflow:

```bash
python -m venv ~/.venvs/airflow && source ~/.venvs/airflow/bin/activate
pip install "apache-airflow==2.9.*" \
    apache-airflow-providers-snowflake apache-airflow-providers-common-sql

export AIRFLOW_HOME=~/airflow-local
export AIRFLOW__CORE__DAGS_FOLDER="$(pwd)/airflow/dags"
export AIRFLOW_DBT_DIR="$(pwd)/anz_banking"
export AIRFLOW_DBT_PROFILES_DIR="$(pwd)/.dbt"

airflow standalone            # UI at http://localhost:8080 (login printed once)
```

Then in the UI: **Admin → Connections → +**, type **Snowflake**, id
`snowflake_default`, and fill in your trial account/user/password/warehouse/
role/database. Trigger the `collections_elt` DAG and watch the three tasks run.

> `dbt build` in the BashOperator needs `dbt-snowflake` importable from the
> Airflow venv, and env vars for the profile. Simplest for local practice:
> `pip install dbt-snowflake` into the same venv and `export SNOWFLAKE_*` before
> `airflow standalone`. (Astronomer's `astro dev init` avoids all this by
> running Airflow in Docker — a nicer option if you know Docker.)

## Deploy to AWS MWAA (what to say in the interview)

MWAA is Airflow-as-a-service. The deployment model:

1. **DAGs bucket (S3)** — MWAA points at an S3 prefix. You *sync* `dags/`
   (including `include/`) there; MWAA picks up changes automatically.
   `aws s3 sync airflow/dags/ s3://<mwaa-bucket>/dags/`
2. **`requirements.txt`** — upload to the env bucket and set its S3 version in
   the MWAA config; MWAA installs the packages on every worker. Pin against the
   Airflow-version constraints file.
3. **`plugins.zip`** (optional) — custom operators/hooks.
4. **Startup script** (optional) — export env vars, e.g. the dbt profile vars.
5. **Execution role (IAM)** — MWAA's role needs S3 (DAGs bucket), CloudWatch
   Logs, and any target permissions.
6. **Connections/secrets** — store the Snowflake connection in **AWS Secrets
   Manager**; MWAA reads it via the secrets backend (no plaintext creds).
7. **Networking** — MWAA runs in your **VPC** (private subnets); reaching
   Snowflake/S3 uses NAT or VPC endpoints.

### Production-grade dbt in Airflow: astronomer-cosmos

Instead of one opaque `dbt build` BashOperator, **[Cosmos](https://astronomer.github.io/astronomer-cosmos/)**
parses your dbt project and renders **each model/test as its own Airflow task**,
so you get per-model retries, logs, and lineage in the Airflow graph. Mentioning
this — and *why* (observability, granular retries) — is a strong interview signal.
