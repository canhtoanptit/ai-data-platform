# AI Data Platform

A full-stack, locally-runnable **data platform**: files land in a warehouse,
**dbt** transforms them into tested marts, a **FastAPI** layer serves them to
applications, and a **React** dashboard (in progress) puts them on screen —
with AI features (natural-language querying over the marts) on the roadmap.

Everything runs on your laptop with `docker compose` — no cloud account, no
credentials. The same dbt models also deploy unchanged to a **Snowflake / AWS**
cloud path (DMS ingestion, MWAA orchestration), because the project is written
cross-database from day one.

The demo dataset is a **debt-collections** domain (cases, payments,
promises-to-pay, agent performance) — realistic enough to make the KPIs and
CDC patterns meaningful, but the platform itself is domain-agnostic.

## Architecture

```
                        LOCAL (docker compose)
┌──────────────┐   dbt seed / COPY   ┌──────────────┐   dbt build    ┌─────────────────┐
│ raw files    │ ──────────────────► │ Postgres 16  │ ─────────────► │ marts           │
│ (CSV / pipe) │                     │ (warehouse)  │  staging →     │ dim_* fct_*     │
└──────────────┘                     └──────────────┘  marts         │ KPI rollups     │
                                                                     └────────┬────────┘
                                                                              │
                                              ┌───────────────────────────────┼─────────┐
                                              ▼                               ▼         │
                                     ┌─────────────────┐             ┌────────────────┐ │
                                     │ FastAPI  :8000  │ ──────────► │ React dashboard│ │
                                     │ /api/metrics/*  │    JSON     │ (Step 3)       │ │
                                     │ /api/cases      │             └────────────────┘ │
                                     └─────────────────┘        AI chat (NL→SQL) planned┘

                        CLOUD (same dbt project, --target dev)
   source DBs ──AWS DMS──► S3 (CSV/pipe) ──COPY/Snowpipe──► Snowflake ──dbt──► marts
                                    orchestrated by AWS MWAA (Airflow)
```

One dbt project, two targets: `local` (Postgres in compose) for development and
demos, `dev` (Snowflake) for the cloud. Details in
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Quickstart (zero credentials)

```bash
make stack-up      # Postgres 16 + API, waits until healthy
make local-build   # dbt: seed → staging → marts → snapshot → tests (all local)

curl -s localhost:8000/api/metrics/summary
# {"total_cases":8,"open_cases":4,"total_delinquent_amount":16630.75,
#  "cure_rate_pct":25.0,"ptp_kept_rate_pct":50.0,"rpc_rate_pct":35.3}
```

Swagger UI: <http://localhost:8000/docs> · dbt lineage docs: `make docs` ·
stop everything: `make stack-down` (the data volume survives).

Where dbt puts things in Postgres (base schema `analytics`, prefixing via dbt's
default `generate_schema_name`):

| Schema | Contents |
|--------|----------|
| `analytics_raw` | seeds (raw CSVs) |
| `analytics_staging` | `stg_*` cleaned views |
| `analytics_marts` | `dim_*`, `fct_*`, `collections_performance` |
| `analytics` | `int_accounts_cdc` (incremental CDC merge model) |
| `snapshots` | SCD2 history |

## Components

| Component | What it does | Where |
|-----------|--------------|-------|
| **dbt project** | seeds → staging → intermediate → marts; tests, docs, SCD2 snapshot, macros, a DMS-style CDC incremental merge | [`anz_banking/`](./anz_banking) |
| **Local warehouse** | Postgres 16 in compose; dbt `local` target | [`docker-compose.yml`](./docker-compose.yml) |
| **API** | FastAPI read layer over the marts: `/api/metrics/*`, `/api/cases` | [`api/`](./api) |
| **Web** | React + TypeScript dashboard | `web/` *(Step 3 — in progress)* |
| **Cloud warehouse path** | Snowflake `COPY INTO` ingestion + file generation (CSV & pipe-delimited) | [`snowflake/`](./snowflake) |
| **Orchestration** | Airflow DAG (ingest → dbt build → unload), written MWAA-deployable | [`airflow/`](./airflow) |
| **Semantic layer** | Cube models over the marts (metrics API alternative) | [`cube/`](./cube) |

Each component folder has its own README.

## Cloud path (Snowflake)

The same models run on a free [Snowflake trial](https://signup.snowflake.com/):

```bash
cp .env.example .env   # fill in your Snowflake details
make debug             # check the connection
make fresh             # deps + full build against Snowflake
```

Setup SQL lives in [`snowflake/01_account_setup.sql`](./snowflake/01_account_setup.sql);
the DMS → S3 → Snowflake ingestion architecture is covered in
[`ARCHITECTURE.md`](./ARCHITECTURE.md).

## Roadmap

- [x] Cross-database dbt project (Postgres local / Snowflake cloud)
- [x] CDC merge model (DMS-style I/U/D stream → current state)
- [x] FastAPI read layer over the marts
- [ ] React + TypeScript dashboard
- [ ] Data catalog + lineage explorer (parsed from dbt artifacts)
- [ ] Pipeline observability (dbt run/test results)
- [ ] AI chat: natural language → SQL over the marts (Claude API)
- [ ] File-upload ingestion UI (CSV / pipe-delimited)
- [ ] Airflow service in compose orchestrating the full loop

## Learning docs

This repo doubles as a study project for data-platform engineering
(dbt, Snowflake, AWS DMS/MWAA, serving layers):

- [`LEARNING.md`](./LEARNING.md) — a milestone-based path with hands-on
  exercises and interview soundbites.
- [`ARCHITECTURE.md`](./ARCHITECTURE.md) — the end-to-end design, the DMS/CDC
  explainer, and the consumption-layer decision guide.
