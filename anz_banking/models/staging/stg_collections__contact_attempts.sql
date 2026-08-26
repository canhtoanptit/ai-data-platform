with source as (
    select * from {{ source('collections', 'raw_contact_attempts') }}
),

renamed as (
    select
        attempt_id,
        case_id,
        agent_id,
        cast(attempt_ts as timestamp) as attempted_at,
        channel,
        outcome,
        -- a "right party contact" (RPC) is a key collections KPI
        case when outcome = 'right_party_contact' then 1 else 0 end as is_right_party_contact
    from source
)

select * from renamed
