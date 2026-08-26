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
