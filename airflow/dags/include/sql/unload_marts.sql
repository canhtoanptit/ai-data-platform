-- Generate the outbound pipe-delimited extract from the performance mart.
-- (Runs from the `collections_elt` DAG's `unload_extracts` task.)
copy into @anz_collections.file_lab.int_stage/exports/collections_performance_
from (select * from anz_collections.marts.collections_performance)
file_format = (format_name = anz_collections.file_lab.ff_pipe)
header = true
overwrite = true
single = false
max_file_size = 16777216;
