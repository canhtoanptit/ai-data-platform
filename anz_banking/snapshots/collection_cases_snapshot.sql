{#
    Snapshot = Slowly Changing Dimension (SCD Type 2).
    A case's status changes over time (open -> resolved / written_off).
    Each `dbt snapshot` run records the *current* status with valid-from /
    valid-to timestamps, so you keep full history even though the source
    table only ever shows the latest state.

    Try it: run `dbt snapshot`, change a status in the seed CSV,
    `dbt seed` again, then `dbt snapshot` again and inspect dbt_valid_from/to.
#}
{% snapshot collection_cases_snapshot %}

{{
    config(
      target_schema='snapshots',
      unique_key='case_id',
      strategy='check',
      check_cols=['case_status', 'days_past_due']
    )
}}

-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_collection_cases') }}
select
    case_id,
    account_id,
    agent_id,
    days_past_due,
    status as case_status
from {{ source('collections', 'raw_collection_cases') }}

{% endsnapshot %}
