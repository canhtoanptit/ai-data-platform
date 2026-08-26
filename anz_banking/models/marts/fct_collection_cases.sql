-- Fact table: one row per collections case, enriched with the account,
-- customer, agent, delinquency bucket, and case-level PTP / contact activity.
-- This is the central model a BI tool or analyst would query.
with cases as (
    select * from {{ ref('stg_collections__cases') }}
),

accounts as (
    select * from {{ ref('stg_collections__accounts') }}
),

customers as (
    select * from {{ ref('stg_collections__customers') }}
),

ptp as (
    select * from {{ ref('int_ptp_per_case') }}
),

contacts as (
    select * from {{ ref('int_contacts_per_case') }}
)

select
    c.case_id,
    c.account_id,
    a.customer_id,
    cust.full_name          as customer_name,
    a.product_type,
    c.agent_id,
    c.opened_date,
    c.resolved_date,
    c.days_past_due,
    {{ delinquency_bucket('c.days_past_due') }} as delinquency_bucket,
    c.delinquent_amount,
    c.case_status,
    -- resolution flags used by the performance mart
    case when c.case_status = 'resolved'    then 1 else 0 end as is_cured,
    case when c.case_status = 'written_off' then 1 else 0 end as is_written_off,
    -- activity rollups
    coalesce(ct.contact_attempts, 0) as contact_attempts,
    coalesce(ct.rpc_count, 0)        as rpc_count,
    coalesce(p.ptp_count, 0)         as ptp_count,
    coalesce(p.ptp_kept_count, 0)    as ptp_kept_count
from cases c
left join accounts a  on c.account_id = a.account_id
left join customers cust on a.customer_id = cust.customer_id
left join ptp p       on c.case_id = p.case_id
left join contacts ct on c.case_id = ct.case_id
