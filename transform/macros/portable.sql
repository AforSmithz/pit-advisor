{% macro surrogate_key(parts) %}
{%- set joined = parts | map('trim') | map('string') | list | join(" || '~' || ") -%}
{%- if target.type == 'athena' -%}
lower(to_hex(md5(to_utf8({{ joined }}))))
{%- else -%}
md5({{ joined }})
{%- endif -%}
{% endmacro %}


{% macro as_text(column) %}
{%- if target.type == 'athena' -%}
cast({{ column }} as varchar)
{%- else -%}
cast({{ column }} as varchar)
{%- endif -%}
{% endmacro %}


{% macro merge_or_replace() %}
{#- iceberg merges on athena, duckdb has no merge so it swaps the rows out -#}
{{ return('merge' if target.type == 'athena' else 'delete+insert') }}
{% endmacro %}


{% macro latest_by(partition_columns, order_column='ingested_at') %}
row_number() over (
    partition by {{ partition_columns | join(', ') }}
    order by {{ order_column }} desc
)
{% endmacro %}


{% macro newer_than_loaded(column='ingested_at') %}
{#- max() over an empty table is null, and null makes the filter drop every row, so a model
    whose first build landed nothing would stay empty for good -#}
where {{ column }} > coalesce(
    (select max({{ column }}) from {{ this }}),
    timestamp '1970-01-01 00:00:00'
)
{% endmacro %}


{% macro list_size(column) %}
{#- trino counts a list with cardinality, duckdb reserves that for maps and uses len -#}
{%- if target.type == 'athena' -%}
cardinality({{ column }})
{%- else -%}
len({{ column }})
{%- endif -%}
{% endmacro %}


{% macro optional_source(source_name, table_name, empty_select) %}
{#- a bronze table that only some lakes have yet (sprint arrives only once a sprint has run).
    duckdb refuses a glob that matches nothing, so it gets an empty typed select instead. on
    athena the catalog registers every contract table up front, so the source always exists -#}
{%- if target.type != 'athena' and execute -%}
{%- set location = env_var('PITADV_LOCAL_LAKE', 'data/local') ~ '/bronze/table=' ~ table_name ~ '/**/*.parquet' -%}
{%- set found = run_query("select count(*) from glob('" ~ location ~ "')").columns[0].values()[0] -%}
{%- if found == 0 -%}
({{ empty_select }})
{%- else -%}
{{ source(source_name, table_name) }}
{%- endif -%}
{%- else -%}
{{ source(source_name, table_name) }}
{%- endif -%}
{% endmacro %}
