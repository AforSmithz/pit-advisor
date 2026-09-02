{{ config(
    materialized='incremental',
    unique_key='citation_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'incident_articles') }}
    {% if is_incremental() %}
    {{ newer_than_loaded() }}
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'document_name', 'entry', 'code']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        as_text('season'), as_text('round'), 'document_name',
        as_text('entry'), 'code'
    ]) }} as citation_id,
    {{ surrogate_key([
        as_text('season'), as_text('round'), 'document_name', as_text('entry')
    ]) }} as incident_id,
    season,
    round,
    document_name,
    document,
    entry,
    code,
    -- the canonical book, so a citation joins whatever way the stewards spelled the name
    book,
    edition,
    regulation,
    ingested_at
from ranked
where recency = 1
