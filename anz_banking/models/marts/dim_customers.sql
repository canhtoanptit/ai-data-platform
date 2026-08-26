-- Customer dimension, enriched with account-level payment behaviour.
with customers as (
    select * from {{ ref('stg_collections__customers') }}
),

accounts as (
    select * from {{ ref('stg_collections__accounts') }}
),

payments as (
    select * from {{ ref('int_payments_per_account') }}
),

account_rollup as (
    select
        a.customer_id,
        count(*)                                                as account_count,
        sum(a.current_balance)                                  as total_balance,
        sum(coalesce(p.total_paid, 0))                          as total_paid,
        max(case when a.account_status = 'delinquent' then 1 else 0 end) as has_delinquent_account
    from accounts a
    left join payments p on a.account_id = p.account_id
    group by 1
)

select
    c.customer_id,
    c.full_name,
    c.email,
    c.state,
    c.signup_date,
    coalesce(r.account_count, 0)         as account_count,
    coalesce(r.total_balance, 0)         as total_balance,
    coalesce(r.total_paid, 0)            as total_paid,
    coalesce(r.has_delinquent_account, 0) = 1 as is_delinquent
from customers c
left join account_rollup r on c.customer_id = r.customer_id
