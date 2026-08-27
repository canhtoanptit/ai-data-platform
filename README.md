# ANZ Collections Platform — data engineering learning project

A realistic, small **debt-collections** pipeline covering the full ANZ JD:
**ETL/ELT with AWS MWAA + dbt**, **file generation & ingestion (CSV +
pipe-delimited)** on **Snowflake**, and the **AWS DMS** ingestion pattern.

> New here? Read [`ARCHITECTURE.md`](./ARCHITECTURE.md) for how the four tools
> fit together, then [`LEARNING.md`](./LEARNING.md) — a ~2–3 week path from zero
> to interview-ready, mapped onto this repo.

## The four JD tools, and where they live

| Tool | Role | Folder |
|------|------|--------|
| **Snowflake** | Warehouse; file ingest (`COPY`) + generation (`COPY INTO @stage`) | [`snowflake/`](./snowflake) |
| **dbt** | Transform raw → tested/documented marts | [`anz_banking/`](./anz_banking) |
| **AWS MWAA** | Managed Airflow orchestrating ingest → dbt → unload | [`airflow/`](./airflow) |
| **AWS DMS** | Source DB → S3 file replication (concept + runbook) | [`ARCHITECTURE.md`](./ARCHITECTURE.md) |

Plus a **consumption layer** — [`cube/`](./cube) — a Cube semantic layer over the
gold marts (serving metrics to an app via REST/GraphQL/SQL). See [`ARCHITECTURE.md`](./ARCHITECTURE.md).

## The data model

A collections platform tracks customers who fall behind on payments and the
work done to recover the debt.

```
seeds (raw CSVs)          staging (views)                 marts (tables)
─────────────────         ────────────────────            ─────────────────────
raw_customers        ->   stg_collections__customers  ->  dim_customers
raw_agents           ->   stg_collections__agents     ->  dim_agents
raw_accounts         ->   stg_collections__accounts  ┐
raw_payments         ->   stg_collections__payments  ┤ int_* (ephemeral)
raw_collection_cases ->   stg_collections__cases     ┼->  fct_collection_cases
raw_contact_attempts ->   stg_collections__contact.. ┤        └-> collections_performance
raw_promises_to_pay  ->   stg_collections__promise.. ┘
```

Key collections concepts modelled: **days past due / delinquency buckets**,
**cure rate**, **promise-to-pay (PTP) kept rate**, **right-party-contact (RPC)
rate**, and **agent/team performance**.

## Prerequisites

1. A free [Snowflake trial](https://signup.snowflake.com/) (30 days, no card).
2. In a Snowflake worksheet, create a database + warehouse to hold the project:
   ```sql
   create warehouse if not exists compute_wh with warehouse_size = 'xsmall'
     auto_suspend = 60 auto_resume = true;
   create database if not exists anz_collections;
   ```
3. `uv` (already used here) and `make`.

## Setup

```bash
cp .env.example .env      # then fill in your Snowflake trial details
make debug                # verify the connection works
make fresh                # deps + seed + run + snapshot + test  (first run)
```

After that, day-to-day:

```bash
make build     # rebuild + test everything
make docs      # browse the model DAG + docs at http://localhost:8080
```

### Run locally (Postgres + Docker)

No Snowflake trial, no credentials, no internet? Snowflake can't run on a
laptop, so [`docker-compose.yml`](./docker-compose.yml) provides a **Postgres 16**
stand-in. The same models, seeds, snapshot and tests build against it via the
`local` target — the project is written to be cross-database.

```bash
make local-up      # start Postgres 16 in Docker, wait until healthy
make local-build   # dbt build --target local  (seed + run + snapshot + test)
```

`make local-down` stops it (the data volume survives). Also available:
`make local-run`, `make local-test`.

Defaults need **no `.env` changes** — `.dbt/profiles.yml` defaults every
`POSTGRES_*` var to the compose values (`platform`/`platform`/`platform` on
`localhost:5432`). Objects land in:

| Schema | Contents |
|--------|----------|
| `analytics_raw` | seeds |
| `analytics_staging` | `stg_*` views |
| `analytics_marts` | `dim_*`, `fct_*`, `collections_performance` tables |
| `analytics` | `int_accounts_cdc` (the incremental CDC model) |
| `snapshots` | `collection_cases_snapshot` |

The `analytics_*` prefixing is dbt's default `generate_schema_name` combining the
target's base schema with each layer's `+schema`. Snowflake (`dev`) stays the
default target and behaves identically, prefixed with your `SNOWFLAKE_SCHEMA`.

#### API

[`api/`](./api) is a **FastAPI** read layer that serves the marts as REST — the
consumption layer an app or agent would call. `make stack-up` brings up Postgres
**and** the API together:

```bash
make stack-up                                   # docker compose up -d --wait
curl -s localhost:8000/api/metrics/summary
# {"total_cases":8,"open_cases":4,"total_delinquent_amount":16630.75,
#  "cure_rate_pct":25.0,"ptp_kept_rate_pct":50.0,"rpc_rate_pct":35.3}
```

Endpoints: `/api/health`, `/api/metrics/summary`, `/api/metrics/performance`,
`/api/cases` (filter by `status`/`bucket`, paged), `/api/cases/{case_id}`. Swagger
UI at <http://localhost:8000/docs>. Also `make api-dev` (autoreload) and
`make api-test`; `make stack-down` stops everything. Details in
[`api/README.md`](./api/README.md).

## Layout

| Path | What it is |
|------|-----------|
| `.env` / `.env.example` | Snowflake credentials (env vars used by `profiles.yml`) |
| `.dbt/profiles.yml` | dbt connection profiles: `dev` (Snowflake) + `local` (Postgres) |
| `docker-compose.yml` | Local Postgres warehouse + the API service |
| `api/` | FastAPI read layer serving the marts as REST (own uv project) |
| `Makefile` | Thin wrapper around `uv run --env-file .env dbt …` |
| `anz_banking/` | The dbt project |
| `anz_banking/seeds/` | Sample raw data as CSVs |
| `anz_banking/models/staging/` | 1:1 cleaned views over sources |
| `anz_banking/models/intermediate/` | Reusable ephemeral building blocks |
| `anz_banking/models/marts/` | Dimensions, facts, and the KPI mart |
| `anz_banking/snapshots/` | SCD2 history of case status |
| `anz_banking/macros/` | Reusable SQL (delinquency bucket logic) |
| `anz_banking/tests/` | A singular (bespoke) data test |
| `snowflake/` | `COPY INTO` ingestion + generation scripts (CSV & pipe) |
| `airflow/` | MWAA-shaped Airflow DAG orchestrating the pipeline |
| `ARCHITECTURE.md` | End-to-end diagram + AWS DMS explainer |
| `LEARNING.md` | Step-by-step learning path across all four tools |
