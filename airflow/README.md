# Orchestration — Airflow / AWS MWAA

Two DAGs, for two different jobs:

| DAG | Runs where | Triggered by | State |
|-----|-----------|--------------|-------|
| **`file_ingest`** | the local compose stack | the dashboard's **Ingest** page (Airflow REST) | runs on your laptop, today |
| **`collections_elt`** | AWS MWAA + Snowflake | a `0 6 * * *` schedule | imports cleanly, **left paused** — it needs Snowflake credentials |

```
Dockerfile.local               # the local Airflow image (compose profile `pipeline`)
dags/
  file_ingest.py               # COPY a staged upload -> scoped dbt build   <- the local one
  collections_elt.py           # ingest -> dbt build -> unload              <- the MWAA one
  include/sql/
    copy_into_raw.sql          # COPY INTO raw landing table
    unload_marts.sql           # COPY INTO @stage (file generation)
requirements.txt               # Python deps MWAA installs
```

## Run it locally (docker compose)

Airflow lives behind an **opt-in compose profile**, because it is by far the
heaviest service in the stack and the other four are useful without it:

```bash
make pipeline-up     # builds the image on first run, then waits until healthy
# Airflow UI: http://localhost:8081   login: admin / admin
make pipeline-down   # stops just Airflow; run history and staged files survive
```

Port **8081**, not 8080, because `make docs` serves dbt's documentation site on
8080 and wanting both at once is normal.

### How `file_ingest` works

```
browser (drag a CSV onto /ingest)
    │  multipart POST
    ▼
FastAPI  /api/ingest        validates (table, extension, size, header),
    │                       writes the file into the shared `uploads` volume,
    │                       then POSTs a dag run over the Airflow REST API
    │  conf = {table, file_path, delimiter}
    ▼
copy_into_raw               COPY FROM STDIN -> analytics_raw.<table>, appending
    │
    ▼
dbt_build_downstream        dbt build --select <staging model>+   (tests included)
```

Four things in there are worth knowing:

* **`schedule=None`.** This DAG has no clock; it runs when something uploads a
  file. That is the shape most ingestion DAGs end up with once a landing-zone
  notification or an API call is what starts them.
* **Only the *path* travels in `conf`**, never the file's contents — so a 5 MB
  CSV never lands in Airflow's metadata database. The API and Airflow share a
  named docker volume (`uploads`) mounted at a different path in each container.
* **The `dbt build` is scoped** to `<model>+`. Rebuilding all ~60 nodes for five
  new payment rows would be slow *and* would make `run_results.json` a record of
  the whole project rather than of this load. Scoped, it takes about 4 seconds
  and the dashboard's **Runs** page shows exactly this pipeline run.
* **The table allow-list is duplicated on purpose.** `TABLE_TO_STAGING` in
  `file_ingest.py` and `INGESTABLE` in `api/app/routers/ingest.py` hold the same
  keys. The API validates early so the browser gets a fast, specific error; the
  DAG revalidates — against the live `information_schema`, not a hardcoded list —
  because it is the thing that actually writes to the warehouse. An execution
  layer that trusts its caller has no authority.

Upload the same file twice and the run goes **red**: the duplicate primary keys
fail dbt's `unique` test, `dbt build` exits non-zero, and nothing downstream is
published. That is the demo's most useful moment, not a bug.

### Two deliberately unproduction-like choices

**SQLite + SequentialExecutor.** One container runs the scheduler and webserver
against a SQLite metadata database, executing one task at a time in-process.
Nothing to configure, nothing else to run — and completely unsuitable for
anything real, because SQLite cannot serve concurrent writers. A production
Airflow uses a Postgres metadata database with the LocalExecutor or
Celery/Kubernetes executors, which is exactly what MWAA provisions for you.

**A fixed `admin` / `admin` login.** `airflow standalone` generates a random
password and prints it once, which is useless when another container has to
authenticate. Fixed credentials are safe *here* because the service is bound to
localhost and holds nothing. On MWAA there are no Airflow users at all: access
is IAM (`mwaa:CreateWebLoginToken`) and connections come from Secrets Manager.

### dbt lives in its own virtualenv

`Dockerfile.local` installs dbt into `/opt/dbt-venv` and symlinks the binary
onto `PATH`, rather than installing it alongside Airflow. This is not caution,
it is arithmetic: `pip install "apache-airflow==2.10.5" dbt-postgres==1.11.0`
fails with `ResolutionImpossible`, because dbt's `dbt-adapters`/`dbt-common`
want `protobuf>=6` and Airflow 2.10's dependency tree caps it below that. pip's
only escape is to walk dbt back to 1.8 — which would install, and then quietly
write an older `manifest.json` schema than the API reads.

Two interpreters, zero shared pins, and dbt stays on the exact version the
repo's own tooling uses. The same isolation, done at larger scale, is what
`astronomer-cosmos` and "run dbt as its own ECS task" both buy you.

## Practise `collections_elt` against a real Snowflake (no Docker)

`make pipeline-up` above gives you a working Airflow but not a working
`collections_elt` — that DAG needs Snowflake, so it stays paused there. To
actually run it, point a local Airflow at your Snowflake trial.

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
