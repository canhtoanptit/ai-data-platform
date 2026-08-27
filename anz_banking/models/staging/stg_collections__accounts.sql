-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_accounts') }}
with source as (
    select * from {{ source('collections', 'raw_accounts') }}
),

renamed as (
    select
        account_id,
        customer_id,
        product_type,
        cast(open_date as date)      as open_date,
        cast(credit_limit as numeric(12,2))    as credit_limit,
        cast(current_balance as numeric(12,2)) as current_balance,
        status                       as account_status
    from source
)

select * from renamed
