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
