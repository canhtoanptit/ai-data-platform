-- ===========================================================================
-- 02 - File formats, stages, and a landing table.
-- These are the building blocks of every file ingestion/generation pipeline.
-- ===========================================================================
use schema anz_collections.file_lab;

-- --- File formats --------------------------------------------------------
-- Pipe-delimited (.psv). This is the format ANZ's JD calls out.
create or replace file format ff_pipe
  type = 'csv'                       -- Snowflake treats delimited text as "csv"
  field_delimiter = '|'
  skip_header = 1
  field_optionally_enclosed_by = '"'
  trim_space = true
  null_if = ('', 'NULL', 'null')
  empty_field_as_null = true
  date_format = 'YYYY-MM-DD';

-- Comma-delimited (.csv).
create or replace file format ff_csv
  type = 'csv'
  field_delimiter = ','
  skip_header = 1
  field_optionally_enclosed_by = '"'
  trim_space = true
  null_if = ('', 'NULL', 'null')
  empty_field_as_null = true
  date_format = 'YYYY-MM-DD';

-- --- Stages --------------------------------------------------------------
-- Internal (Snowflake-managed) stage: use for hands-on PUT/GET practice.
create or replace stage int_stage;

-- External S3 stage: this is what a DMS / file-drop pipeline reads from in AWS.
-- Needs a storage integration granting Snowflake access to the bucket.
-- Template only (fill in bucket + role ARN, then uncomment):
--
-- create or replace storage integration s3_int
--   type = external_stage
--   storage_provider = 's3'
--   enabled = true
--   storage_aws_role_arn = 'arn:aws:iam::<account_id>:role/<snowflake_s3_role>'
--   storage_allowed_locations = ('s3://<bucket>/collections/');
--
-- create or replace stage ext_stage
--   url = 's3://<bucket>/collections/'
--   storage_integration = s3_int
--   file_format = ff_pipe;

-- --- Landing table -------------------------------------------------------
-- Where ingested account files land. Note the ingestion-metadata columns:
-- capturing the source filename + load time is a data-engineering best practice
-- (auditability, idempotent reloads, debugging bad files).
create or replace table accounts_landing (
  account_id      integer,
  customer_id     integer,
  product_type    string,
  open_date       date,
  credit_limit    number(12,2),
  current_balance number(12,2),
  status          string,
  _source_file    string,
  _loaded_at      timestamp_ntz default current_timestamp()
);
