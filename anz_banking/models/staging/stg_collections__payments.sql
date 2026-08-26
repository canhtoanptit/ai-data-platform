with source as (
    select * from {{ source('collections', 'raw_payments') }}
),

renamed as (
    select
        payment_id,
        account_id,
        cast(payment_date as date)     as payment_date,
        cast(amount as number(12,2))   as payment_amount,
        method                         as payment_method
    from source
)

select * from renamed
