-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_customers') }}
with source as (
    select * from {{ source('collections', 'raw_customers') }}
),

renamed as (
    select
        customer_id,
        first_name,
        last_name,
        first_name || ' ' || last_name as full_name,
        lower(email)                   as email,
        cast(date_of_birth as date)    as date_of_birth,
        state,
        cast(signup_date as date)      as signup_date
    from source
)

select * from renamed
