-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_promises_to_pay') }}
with source as (
    select * from {{ source('collections', 'raw_promises_to_pay') }}
),

renamed as (
    select
        ptp_id,
        case_id,
        cast(promised_date as date)             as promised_date,
        cast(promised_amount as numeric(12,2))   as promised_amount,
        kept_flag                               as ptp_status  -- true / false / pending
    from source
)

select * from renamed
