-- Aggregated performance mart: collections KPIs by agent team and
-- delinquency bucket. This is the "answer the business question" model.
with cases as (
    select * from {{ ref('fct_collection_cases') }}
),

agents as (
    select * from {{ ref('dim_agents') }}
)

select
    ag.team,
    c.delinquency_bucket,
    count(*)                                          as case_count,
    sum(c.delinquent_amount)                          as delinquent_amount,
    sum(c.is_cured)                                   as cured_cases,
    sum(c.is_written_off)                             as written_off_cases,
    -- cure rate = share of cases resolved rather than written off / still open
    round(sum(c.is_cured) * 100.0 / nullif(count(*), 0), 1)      as cure_rate_pct,
    -- PTP kept rate across all promises in the segment
    round(sum(c.ptp_kept_count) * 100.0 / nullif(sum(c.ptp_count), 0), 1) as ptp_kept_rate_pct,
    -- right-party-contact rate per attempt
    round(sum(c.rpc_count) * 100.0 / nullif(sum(c.contact_attempts), 0), 1) as rpc_rate_pct
from cases c
left join agents ag on c.agent_id = ag.agent_id
group by 1, 2
order by 1, 2
