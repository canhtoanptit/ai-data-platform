-- These raw tables are loaded by `dbt seed`, but source() creates no DAG edge to
-- the seed, so `dbt build` would race it. A ref() inside a comment adds the edge
-- (seed runs first) without changing the compiled SQL.
-- depends_on: {{ ref('raw_agents') }}
with source as (
    select * from {{ source('collections', 'raw_agents') }}
),

renamed as (
    select
        agent_id,
        agent_name,
        team,
        cast(hire_date as date) as hire_date
    from source
)

select * from renamed
