-- ===========================================================================
-- 03 - INGESTION: load a pipe-delimited file into Snowflake with COPY INTO.
-- ===========================================================================
use schema anz_collections.file_lab;

-- 1) Upload the local sample file to the internal stage.
--    PUT runs from SnowSQL or a driver (NOT the Snowsight worksheet):
--
--      snowsql -a <account> -u <user> -q "put \
--        file://<repo>/snowflake/sample_files/accounts.psv \
--        @anz_collections.file_lab.int_stage auto_compress=false overwrite=true"
--
--    (Or use Snowsight: Data > Databases > ANZ_COLLECTIONS > FILE_LAB >
--     Stages > INT_STAGE > "+ Files" to upload from the browser.)

-- 2) See what's on the stage.
list @int_stage;

-- 3) Validate BEFORE loading — returns parsing errors without inserting rows.
copy into accounts_landing
from @int_stage/accounts.psv
file_format = (format_name = ff_pipe)
validation_mode = return_errors;

-- 4) Load it. We use the COPY-with-SELECT (transform) form so we can capture
--    metadata$filename into the audit column. $1..$7 are the file's columns.
copy into accounts_landing
  (account_id, customer_id, product_type, open_date,
   credit_limit, current_balance, status, _source_file)
from (
  select $1, $2, $3, $4, $5, $6, $7, metadata$filename
  from @int_stage/accounts.psv (file_format => 'ff_pipe')
)
on_error = 'abort_statement';   -- alternatives: 'skip_file', 'continue'

-- 5) Verify the load.
select count(*) as rows_loaded from accounts_landing;
select * from accounts_landing limit 10;

-- 6) Inspect load history — your go-to for troubleshooting failed COPYs.
select file_name, status, row_count, row_parsed, first_error_message
from table(information_schema.copy_history(
  table_name => 'ACCOUNTS_LANDING',
  start_time => dateadd('hour', -1, current_timestamp())));

-- --- CSV is identical -----------------------------------------------------
-- Same commands, but point at a .csv file and use file_format = (format_name = ff_csv).

-- --- Production note: Snowpipe ------------------------------------------
-- For continuous ingestion (files arriving in S3 all day) you'd wrap the COPY
-- in a PIPE with auto_ingest, triggered by an S3 event notification:
--   create pipe accounts_pipe auto_ingest = true as
--     copy into accounts_landing from @ext_stage file_format = (format_name = ff_pipe);
-- MWAA/DMS lands the files; Snowpipe loads them without a running warehouse.
