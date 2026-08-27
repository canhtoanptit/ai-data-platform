{{
  config(
    materialized = 'incremental',
    incremental_strategy = 'merge',
    unique_key = 'account_id',
    on_schema_change = 'sync_all_columns'
  )
}}

/*
  CDC merge example — the DMS pattern.

  `raw_accounts_cdc` simulates an AWS DMS change-data-capture feed: an
  append-only stream of change events, each tagged with an operation
  (op = I/U/D) and a change timestamp. This model collapses that stream into
  the CURRENT state — one row per account = its most recent change — with a
  soft-delete flag.

  Why incremental + merge:
    - materialized='incremental' + strategy='merge': each run processes only
      NEW events, then upserts them into the existing table by
      unique_key=account_id (no full rebuild of a huge history every run).
    - is_incremental(): false on first run / `--full-refresh` (read everything);
      true afterwards (read only events newer than our high-water mark).
    - Deletes: a DMS 'D' event sets is_deleted = true (soft delete). Downstream
      models filter `where not is_deleted`. Hard-delete alternative below.

  Cross-database note: 'merge' needs no adapter branch here. dbt-postgres lists
  merge as a supported strategy and PG15+ has native MERGE, so this exact config
  runs on Snowflake and Postgres (a second run logs "MERGE n", not an INSERT).

  Try it: `make build`, then append a new event to seeds/raw_accounts_cdc.csv,
  `make seed`, `make run -s int_accounts_cdc` — only the new event is merged.
*/

-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_accounts_cdc') }}
with cdc as (
    select
        op,
        cdc_timestamp,
        account_id,
        customer_id,
        product_type,
        open_date,
        credit_limit,
        current_balance,
        status
    from {{ source('collections', 'raw_accounts_cdc') }}

    {% if is_incremental() %}
    -- high-water mark: only events newer than what's already loaded.
    -- (Simplification: a global watermark assumes in-order delivery per key.)
    -- plain `timestamp` casts on both: Snowflake TIMESTAMP defaults to NTZ,
    -- and Postgres has no TIMESTAMP_NTZ type name at all.
    where cdc_timestamp > (
        select coalesce(max(_cdc_timestamp), cast('1900-01-01' as timestamp))
        from {{ this }}
    )
    {% endif %}
),

-- Most recent change per account within this batch.
-- Snowflake's idiom here is QUALIFY row_number() over (...) = 1, which filters
-- on a window function without a subquery; this ranked-CTE + WHERE is the
-- portable form that also runs on Postgres.
ranked as (
    select
        cdc.*,
        row_number() over (
            partition by account_id order by cdc_timestamp desc
        ) as _row_num
    from cdc
),

latest as (
    select * from ranked where _row_num = 1
)

select
    account_id,
    customer_id,
    product_type,
    cast(open_date as date)               as open_date,
    -- numeric(p,s) not number(p,s): NUMERIC is a Snowflake synonym for NUMBER
    -- and is also the Postgres type name, so one spelling works on both.
    cast(credit_limit as numeric(12,2))    as credit_limit,
    cast(current_balance as numeric(12,2)) as current_balance,
    status                                as account_status,
    op                                    as _cdc_op,
    (op = 'D')                            as is_deleted,
    cdc_timestamp                         as _cdc_timestamp
from latest

/*
  HARD-DELETE alternative: to physically remove 'D' rows instead of flagging
  them, add a post-hook that deletes soft-deleted rows after the merge:

    config(
      ...,
      post_hook="delete from {{ this }} where is_deleted"
    )
*/
