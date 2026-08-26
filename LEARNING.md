# Learning path — data engineering for the ANZ Collections Platform role

**Target JD:** develop/maintain ETL/ELT pipelines using **AWS MWAA** and
**dbt**; build **file generation & ingestion** pipelines (**CSV + pipe-
delimited**); hands-on with **Snowflake, dbt, AWS DMS, MWAA**.

This is a ~2–3 week plan. Every milestone uses **this repo** so you learn by
doing. See `ARCHITECTURE.md` for how the four tools fit together.

Priority order (by how central each is to the JD and how much you can practise
for free): **Snowflake + dbt → file pipelines → MWAA/Airflow → DMS (concept)**.

---

## Milestone 0 — Get the transform layer running (½ day)

- [ ] Snowflake trial + run `snowflake/01_account_setup.sql`.
- [ ] `cp .env.example .env`, fill it in, `make debug` → green.
- [ ] `make fresh` → seeds load, models build, tests pass.
- [ ] `make docs` → explore the lineage from `collections_performance` back to seeds.

**You can explain:** dbt project/profile/target; how dbt turns SQL into
Snowflake objects; the model DAG.

---

## Milestone 1 — Snowflake fundamentals (1 day)

- [ ] **Storage vs compute**; warehouses (size, `auto_suspend`, per-second billing).
- [ ] Databases → schemas → tables; find the objects dbt + the scripts created.
- [ ] **RBAC**: roles, `use role`, grants.
- [ ] **Zero-copy clone** & **Time Travel** (`at(offset => -60)`).
- [ ] Micro-partitions & pruning (concept) — why no classic indexes.

**Soundbite:** *"Snowflake decouples storage and compute, so I size a warehouse
per workload, pay per-second, and let dbt materialise transforms inside it."*

---

## Milestone 2 — dbt core concepts (2–3 days)

Open each file as you read. All live in `anz_banking/`.

- [ ] **Sources** (`models/staging/_collections__sources.yml`).
- [ ] **Materializations** (`dbt_project.yml`): view / table / ephemeral /
      incremental — *when* each. Staging=view, marts=table, intermediate=ephemeral.
- [ ] **`ref()` / `source()`** build the DAG + run order. Break one, watch it fail.
- [ ] **3-layer pattern** staging → intermediate → marts (draw it on a whiteboard).
- [ ] **Tests**: generic (`unique`, `not_null`, `relationships`,
      `accepted_values`) + a **singular** test in `tests/`. `make test`.
- [ ] **Docs & DAG** (`make docs`), **macros** (`macros/delinquency_bucket.sql`),
      **packages** (`packages.yml`, `dbt_utils`), **snapshots** (SCD2, `make snapshot`).
- [ ] `dbt build` vs `run/test/seed/snapshot`; graph selectors (`-s model+`);
      `--full-refresh`; **dev vs prod targets**.

> dbt 1.12 prints a harmless deprecation about nesting generic-test args under
> `arguments:`. The classic syntax here still works and matches the tutorials.

---

## Milestone 3 — File pipelines: ingestion + generation (1–2 days)  ★ JD core

Work through `snowflake/` scripts 02→04.

- [ ] **File formats** for pipe (`|`) and CSV: delimiter, `skip_header`,
      `field_optionally_enclosed_by`, `null_if`, `date_format`.
- [ ] **Stages**: internal (PUT/GET practice) vs external S3 (storage integration).
- [ ] **`COPY INTO` (ingest)**: `validation_mode`, `on_error`
      (`abort_statement`/`skip_file`/`continue`), capturing `metadata$filename`,
      reading `copy_history` to debug.
- [ ] **`COPY INTO @stage` (generate)**: `header`, `single`, `max_file_size`,
      pipe vs CSV output.
- [ ] **Snowpipe** (concept): continuous auto-ingest from S3 events.
- [ ] Load `sample_files/accounts.psv`, then generate a pipe extract of a mart.

**Soundbite:** *"Ingestion is a `COPY INTO` from a stage with a file format and
error handling; generation is `COPY INTO @stage` with `header`/`single`. For
continuous loads I'd use Snowpipe on S3 event notifications."*

---

## Milestone 4 — Orchestration with MWAA / Airflow (2–3 days)  ★ JD core

See `airflow/README.md`.

- [ ] Run Airflow locally (`airflow standalone`), add the `snowflake_default`
      connection, trigger `collections_elt`, watch **ingest → dbt build → unload**.
- [ ] **Airflow concepts**: DAG, task, operator, dependencies (`>>`), schedule,
      `catchup`, retries, XComs, sensors, connections, TaskFlow API.
- [ ] **Operators used**: `SQLExecuteQueryOperator` (Snowflake), `BashOperator` (dbt).
- [ ] **MWAA specifics** (be able to describe): DAGs S3 bucket, `requirements.txt`,
      `plugins.zip`, startup script, execution IAM role, **Secrets Manager** for
      connections, VPC networking.
- [ ] **Running dbt in Airflow**: BashOperator vs ECS/Fargate vs
      **astronomer-cosmos** (per-model tasks) — know the trade-offs.

**Soundbite:** *"On MWAA I sync DAGs to the S3 bucket, manage Python deps via
`requirements.txt` pinned to the Airflow constraints, keep the Snowflake
connection in Secrets Manager, and orchestrate ingest → dbt build → unload with
retries and alerting."*

---

## Milestone 5 — AWS DMS (1 day, mostly concept)  ★ JD core

Read the DMS section in `ARCHITECTURE.md`.

- [ ] Replication instance, source/target **endpoints**, **migration task**.
- [ ] **Full load vs CDC vs full-load+CDC**; the CDC `Op` column (I/U/D) and how
      you apply changes downstream (merge / dbt snapshots / incremental).
- [ ] **Table mappings & transformation rules**; schema drift; LOB handling.
- [ ] Monitoring with **CloudWatch** (CDC latency, task state).
- [ ] Why DMS → **S3** → Snowflake (decoupling, replayable raw archive).

**Soundbite:** *"DMS does full-load + CDC from the source DB into S3 as
pipe-delimited files; Snowflake ingests them via external stage + Snowpipe;
CDC deletes/updates are reconciled with incremental models or snapshots."*

---

## Milestone 6 — Extend it yourself + mock interview (2–3 days)

Pick a few — this is what makes you *sound* hands-on:

- [ ] Study the **CDC merge** model already in the repo
      (`anz_banking/models/intermediate/int_accounts_cdc.sql`): incremental +
      merge collapsing a DMS I/U/D stream to current state. Append an event to
      `seeds/raw_accounts_cdc.csv`, `make seed`, rerun it, and confirm only the
      new event merges. This is the single most JD-relevant example.
- [ ] Convert a large fact to **incremental** (`is_incremental()`, `unique_key`).
- [ ] Add a **roll-rate** metric off the snapshot history.
- [ ] Add **`source freshness`** thresholds.
- [ ] Add a new source end-to-end (file → stage → COPY → staging → mart → extract).
- [ ] Add a **Cosmos**-based version of the DAG (per-model tasks).
- [ ] Rehearse the soundbites above out loud; draw `ARCHITECTURE.md` from memory.

**Whiteboard drill:** *"A pipe-delimited file lands in S3 from DMS — walk me
through to a dashboard-ready table, and how you'd schedule, test, and monitor
it."* You should be able to narrate every arrow in `ARCHITECTURE.md`.

---

## Reference links

- dbt Fundamentals: https://learn.getdbt.com · dbt project structure:
  https://docs.getdbt.com/best-practices/how-we-structure/1-guide-overview
- Snowflake key concepts: https://docs.snowflake.com/en/user-guide/intro-key-concepts
- Snowflake `COPY INTO`: https://docs.snowflake.com/en/sql-reference/sql/copy-into-table
- MWAA: https://docs.aws.amazon.com/mwaa/latest/userguide/what-is-mwaa.html
- Airflow: https://airflow.apache.org/docs/apache-airflow/stable/tutorial/
- astronomer-cosmos: https://astronomer.github.io/astronomer-cosmos/
- AWS DMS: https://docs.aws.amazon.com/dms/latest/userguide/Welcome.html
