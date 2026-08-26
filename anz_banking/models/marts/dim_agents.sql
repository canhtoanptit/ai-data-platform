-- Simple agent dimension.
select
    agent_id,
    agent_name,
    team,
    hire_date
from {{ ref('stg_collections__agents') }}
