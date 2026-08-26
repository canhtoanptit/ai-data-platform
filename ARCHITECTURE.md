# End-to-end architecture (mapped to the ANZ JD)

The JD is a data-engineering role: build/maintain **ETL/ELT pipelines with AWS
MWAA + dbt**, do **file generation & ingestion (CSV / pipe-delimited)**, and be
hands-on with **Snowflake, dbt, AWS DMS, MWAA**. Here's how the whole thing fits
together and where each part lives in this repo.

```
 ┌──────────────┐   full load + CDC     ┌──────────────┐   COPY INTO / Snowpipe   ┌─────────────────────┐
 │  Source DBs  │ ───── AWS DMS ──────► │   S3 bucket  │ ───────────────────────► │  Snowflake  RAW     │
 │ (Oracle/PG/  │   (pipe/CSV files)    │  (landing)   │   (external stage)       │  landing tables     │
 │  MySQL)      │                       └──────────────┘                          └─────────┬───────────┘
 └──────────────┘                                                                           │ dbt build
                                                                                            ▼
                                                                        ┌───────────────────────────────┐
                                                                        │ staging → intermediate → marts │
                                                                        │  (dim_*, fct_*, performance)   │
                                                                        └───────────────┬───────────────┘
                                                                                        │ COPY INTO @stage
                                                                                        ▼
                                                                        ┌───────────────────────────────┐
                                                                        │ outbound extract files         │
                                                                        │ (CSV / pipe-delimited → S3)    │
                                                                        └───────────────────────────────┘

        ▲                                                                                   
        └────────────────── AWS MWAA (Airflow) orchestrates every step on a schedule ───────┘
```

## The four tools, and where to find them here

| Tool | Role in the pipeline | In this repo |
|------|----------------------|--------------|
| **AWS DMS** | Replicate source databases to S3 as files (full load + ongoing CDC). | Conceptual — see the "DMS" section below + `snowflake/README.md`. |
| **Snowflake** | Cloud warehouse. Ingest files (`COPY`/Snowpipe), store raw→marts, generate extract files. | `snowflake/*.sql` |
| **dbt** | Transform raw → tested, documented marts (the "T"/"EL-T"). | `anz_banking/` |
| **AWS MWAA** | Managed Airflow. Orchestrate ingest → dbt → unload, with scheduling, retries, alerting. | `airflow/` |

## AWS DMS in one page

**What it is:** a managed service that migrates and **continuously replicates**
data from a source database to a target, with minimal downtime.

**Core objects:**
- **Replication instance** — the compute that runs the migration.
- **Source & target endpoints** — connection configs (e.g. source = Oracle,
  target = **S3**).
- **Migration task** — what to move and how. Three types:
  - **Full load** — one-time copy of existing data.
  - **CDC (change data capture)** — stream ongoing inserts/updates/deletes.
  - **Full load + CDC** — the common choice: snapshot, then keep in sync.

**Why target S3 (not Snowflake directly)?** DMS → S3 gives you cheap, durable
landing files (CSV/parquet) that Snowflake ingests with `COPY`/Snowpipe. It
decouples extraction from loading and gives you a replayable raw archive.

**Things interviewers probe:**
- CDC captures deletes and updates — how do you apply them downstream? (DMS can
  write CDC with an `Op` column: I/U/D; you merge in Snowflake or with dbt
  snapshots/incremental models.)
- **Table mappings & transformation rules** (include/exclude schemas, rename).
- Handling schema drift and LOB columns.
- Monitoring via **CloudWatch** (`CDCLatencySource/Target`, task status).

### CDC → current state (worked example)

DMS writes CDC as an **append-only stream** of change events, each with an
`Op` column (`I`/`U`/`D`) and a commit timestamp. Turning that stream into a
current-state table is a classic dbt task — see
`anz_banking/models/intermediate/int_accounts_cdc.sql`:

- Seed `raw_accounts_cdc` simulates the DMS feed (inserts, updates, a delete).
- The model is **`materialized='incremental'`, `incremental_strategy='merge'`,
  `unique_key='account_id'`**: each run processes only new events
  (`is_incremental()` high-water mark) and **merges** them into the table.
- It keeps one row per account (latest change via `QUALIFY row_number()`), and
  a `'D'` event sets **`is_deleted`** (soft delete); downstream filters
  `where not is_deleted`. A hard-delete `post_hook` alternative is in the file.

**Soundbite:** *"DMS lands full-load + CDC files in S3; I COPY them into an
append-only raw table, then a dbt incremental merge model collapses the I/U/D
stream to current state — soft-deleting on `Op = 'D'` — so I only process new
changes each run instead of rebuilding history."*

**Free-tier note:** a real DMS + MWAA stack costs money to run. For interview
prep, understand the architecture above and be able to draw it. If you want a
real hands-on lab, ask and I'll add Terraform to stand up S3 + DMS + MWAA.

## Consumption / serving layer

The gold marts are the *end* of the pipeline but the *start* of consumption.
How an application reads them depends on the **type** of consumption — and
matching the two is the interview-valuable point:

| Consumption | Example | Serve it with | Why |
|---|---|---|---|
| **Analytical** | dashboard of `collections_performance` by team/bucket | BI tool, or a **semantic layer (Cube)** | consistent metrics, caching, API |
| **Operational** | agent app showing a customer's live case/balance/PTP | source system or a **serving DB / reverse ETL** copy | OLAP marts aren't a low-latency, high-concurrency store |

**Key nuance:** don't point a high-traffic operational app straight at Snowflake
marts — it's slow and expensive per query. Snowflake is an OLAP warehouse, not
an OLTP serving store.

```
Snowflake MARTS ──► Cube (semantic model + pre-aggregations) ──► app (REST/GraphQL/SQL)
        └─────────► BI tools (Power BI / Metabase) ──► analysts
        └─────────► reverse ETL / serving DB ──► operational app screens
```

### Cube (worked example)

`cube/` contains a runnable **Cube** semantic layer over the gold marts:
- `cube/model/cubes/collection_cases.yml` — dimensions + measures
  (`cure_rate`, `ptp_kept_rate`) over `fct_collection_cases`, plus a
  **pre-aggregation** (materialized rollup Cube serves app queries from instead
  of hitting Snowflake each time).
- `cube/model/cubes/agents.yml` — `dim_agents`, joined in.
- `cube/docker-compose.yml` + `cube/.env.example` — run it locally against your
  trial; the app calls REST / GraphQL / SQL.

See `cube/README.md` for how to run it and how an app queries it.

**Soundbite:** *"Gold marts feed analytics through a semantic layer like Cube —
metrics defined once, pre-aggregations for fast high-concurrency reads. The
operational agent app reads from the source system or a serving store, not the
warehouse directly."*
