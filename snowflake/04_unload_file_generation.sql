-- ===========================================================================
-- 04 - FILE GENERATION: unload a mart to CSV / pipe-delimited files.
-- "Build file generation pipelines using CSV and pipe-delimited formats."
-- ===========================================================================
use schema anz_collections.file_lab;

-- Generate a PIPE-DELIMITED extract of a mart into the internal stage.
-- Snowflake splits output across files up to max_file_size automatically.
copy into @int_stage/exports/collections_performance_pipe_
from (select * from anz_collections.marts.collections_performance)
file_format = (format_name = ff_pipe)
header = true
overwrite = true
single = false
max_file_size = 16777216;   -- ~16 MB per file

-- Generate a CSV extract, forced into a single file.
copy into @int_stage/exports/collections_performance_
from (select * from anz_collections.marts.collections_performance)
file_format = (format_name = ff_csv)
header = true
overwrite = true
single = true;

-- What did we write?
list @int_stage/exports/;

-- Download to your machine (SnowSQL / driver, not the worksheet):
--   snowsql ... -q "get @anz_collections.file_lab.int_stage/exports/ file:///tmp/exports/"
--
-- To an EXTERNAL S3 stage instead (what a real downstream-file pipeline does):
--   copy into @ext_stage/exports/collections_performance_
--   from (select * from anz_collections.marts.collections_performance)
--   file_format = (format_name = ff_pipe) header = true overwrite = true;
