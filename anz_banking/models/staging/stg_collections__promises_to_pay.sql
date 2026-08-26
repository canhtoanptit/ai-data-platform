with source as (
    select * from {{ source('collections', 'raw_promises_to_pay') }}
),

renamed as (
    select
        ptp_id,
        case_id,
        cast(promised_date as date)             as promised_date,
        cast(promised_amount as number(12,2))   as promised_amount,
        kept_flag                               as ptp_status  -- true / false / pending
    from source
)

select * from renamed
