-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_payments') }}
with source as (
    select * from {{ source('collections', 'raw_payments') }}
),

renamed as (
    select
        payment_id,
        account_id,
        cast(payment_date as date)     as payment_date,
        cast(amount as numeric(12,2))   as payment_amount,
        method                         as payment_method
    from source
)

select * from renamed
