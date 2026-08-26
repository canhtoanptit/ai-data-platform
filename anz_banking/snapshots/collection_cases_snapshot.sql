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

select
    case_id,
    account_id,
    agent_id,
    days_past_due,
    status as case_status
from {{ source('collections', 'raw_collection_cases') }}

{% endsnapshot %}
