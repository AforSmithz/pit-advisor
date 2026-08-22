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
