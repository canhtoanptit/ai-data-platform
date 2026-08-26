-- ===========================================================================
-- 01 - One-time account setup. Run in a Snowsight worksheet as ACCOUNTADMIN.
-- ===========================================================================

-- Compute. XSMALL is plenty for learning; auto-suspend fast to save credits.
create warehouse if not exists compute_wh
  warehouse_size = 'xsmall'
  auto_suspend = 60
  auto_resume = true
  initially_suspended = true;

-- Database that holds everything for this project.
create database if not exists anz_collections;

-- Dedicated schema for the file ingestion / generation lab. Kept separate from
-- the schemas dbt manages (raw / staging / marts / snapshots) so experiments
-- here never clash with `dbt build`.
create schema if not exists anz_collections.file_lab;

-- -- Optional: mirror a real setup with a dedicated transform role for dbt.
-- create role if not exists transformer;
-- grant usage on warehouse compute_wh to role transformer;
-- grant usage on database anz_collections to role transformer;
-- grant all on schema anz_collections.file_lab to role transformer;
-- grant role transformer to user <your_user>;
