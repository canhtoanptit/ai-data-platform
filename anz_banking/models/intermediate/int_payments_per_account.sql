-- Total amount and count of payments received, per account.
-- Ephemeral: dbt inlines this as a CTE wherever it's ref()'d.
with payments as (
    select * from {{ ref('stg_collections__payments') }}
)

select
    account_id,
    count(*)              as payment_count,
    sum(payment_amount)   as total_paid,
    max(payment_date)     as last_payment_date
from payments
group by 1
