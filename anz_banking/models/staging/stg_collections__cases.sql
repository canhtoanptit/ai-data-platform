-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_collection_cases') }}
with source as (
    select * from {{ source('collections', 'raw_collection_cases') }}
),

renamed as (
    select
        case_id,
        account_id,
        agent_id,
        cast(opened_date as date)              as opened_date,
        days_past_due,
        cast(delinquent_amount as numeric(12,2)) as delinquent_amount,
        status                                 as case_status,
        cast(resolved_date as date)            as resolved_date
    from source
)

select * from renamed
