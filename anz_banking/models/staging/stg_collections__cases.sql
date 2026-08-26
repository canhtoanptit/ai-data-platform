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
        cast(delinquent_amount as number(12,2)) as delinquent_amount,
        status                                 as case_status,
        cast(resolved_date as date)            as resolved_date
    from source
)

select * from renamed
