-- Singular (bespoke) test: a data-quality rule expressed as a query that
-- must return ZERO rows to pass. Here: any case marked 'resolved' or
-- 'written_off' must have a resolved_date.
select
    case_id,
    case_status,
    resolved_date
from {{ ref('stg_collections__cases') }}
where case_status in ('resolved', 'written_off')
  and resolved_date is null
