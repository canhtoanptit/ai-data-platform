with source as (
    select * from {{ source('collections', 'raw_accounts') }}
),

renamed as (
    select
        account_id,
        customer_id,
        product_type,
        cast(open_date as date)      as open_date,
        cast(credit_limit as number(12,2))    as credit_limit,
        cast(current_balance as number(12,2)) as current_balance,
        status                       as account_status
    from source
)

select * from renamed
