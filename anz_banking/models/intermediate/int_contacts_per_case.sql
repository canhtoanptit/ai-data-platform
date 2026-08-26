-- Contact-attempt activity rolled up to one row per case.
with contacts as (
    select * from {{ ref('stg_collections__contact_attempts') }}
)

select
    case_id,
    count(*)                       as contact_attempts,
    sum(is_right_party_contact)    as rpc_count,   -- right-party contacts
    max(attempted_at)              as last_contacted_at
from contacts
group by 1
