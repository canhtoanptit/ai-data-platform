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
