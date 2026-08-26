# Snowflake file pipelines (ingestion + generation)

Hands-on scripts for the JD line *"build file generation and ingestion
pipelines using CSV and pipe-delimited formats."* Everything here runs on a
free Snowflake trial and is kept in its own `file_lab` schema so it never
clashes with the dbt project.

Run the scripts in order in a Snowsight worksheet:

| Script | What it teaches |
|--------|-----------------|
| `01_account_setup.sql` | warehouse, database, schema (one-time) |
| `02_file_formats_and_stages.sql` | **file formats** (pipe + csv), **stages** (internal + external S3 template), a landing table with audit columns |
| `03_ingest_copy_into.sql` | **COPY INTO** to load a `.psv` file: validate → load with `metadata$filename` → verify → `copy_history` |
| `04_unload_file_generation.sql` | **COPY INTO @stage** to generate pipe/csv extract files from a mart |

Sample files to ingest live in `sample_files/` (`accounts.psv`, `accounts.csv`).

## Getting a file onto a stage

`PUT`/`GET` don't work from the browser worksheet — use one of:

- **Snowsight UI**: Data → Databases → `ANZ_COLLECTIONS` → `FILE_LAB` → Stages →
  `INT_STAGE` → **+ Files** (easiest for internal stages).
- **SnowSQL** (CLI): `brew install --cask snowflake-snowsql`, then the `put`
  command shown in `03_ingest_copy_into.sql`.

## How this maps to the AWS pipeline

```
source DB ──AWS DMS──► S3 (pipe-delimited files) ──► Snowflake external stage
                                                        │
                                                COPY INTO / Snowpipe
                                                        ▼
                                                 raw landing tables
                                                        │  dbt build
                                                        ▼
                                            staging → marts  ──COPY INTO @stage──► outbound extract files
```

- **AWS DMS** replicates a source database (Oracle/Postgres/MySQL) to **S3** as
  CSV/parquet — full load + change data capture (CDC).
- **Snowflake external stage + COPY/Snowpipe** ingests those files (scripts 02–03).
- **dbt** transforms raw → marts.
- **COPY INTO @stage** (script 04) generates outbound files for downstream systems.
- **MWAA (Airflow)** orchestrates the whole thing on a schedule — see `../airflow/`.
