-- Promise-to-pay outcomes rolled up to one row per case.
with ptp as (
    select * from {{ ref('stg_collections__promises_to_pay') }}
)

select
    case_id,
    count(*)                                              as ptp_count,
    sum(case when ptp_status = 'true'  then 1 else 0 end) as ptp_kept_count,
    sum(case when ptp_status = 'false' then 1 else 0 end) as ptp_broken_count,
    sum(promised_amount)                                  as total_promised_amount
from ptp
group by 1
