-- Ingest the latest accounts file from the stage into the raw landing table.
-- (Runs from the `collections_elt` DAG's `ingest_files` task.)
copy into anz_collections.file_lab.accounts_landing
  (account_id, customer_id, product_type, open_date,
   credit_limit, current_balance, status, _source_file)
from (
  select $1, $2, $3, $4, $5, $6, $7, metadata$filename
  from @anz_collections.file_lab.int_stage/accounts.psv (file_format => 'ff_pipe')
)
on_error = 'skip_file';
