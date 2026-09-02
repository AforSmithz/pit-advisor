{{ config(
    materialized='incremental',
    unique_key='incident_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'incidents') }}
    {% if is_incremental() %}
    {{ newer_than_loaded() }}
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'document', 'entry']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        as_text('season'), as_text('round'), as_text('document'), as_text('entry')
    ]) }} as incident_id,
    {{ surrogate_key([as_text('season'), as_text('round')]) }} as event_id,
    season,
    round,
    document,
    entry,
    kind,
    issued,
    car,
    driver,
    competitor,
    session,
    fact,
    charge,
    outcome,
    reason,
    read_by,
    -- a value a model quoted that was not in the document is not stored, so the count of what
    -- it could not ground travels with the row rather than being silently absent
    {{ list_size('unverified') }} as unverified_fields,
    raw_key,
    ingested_at
from ranked
where recency = 1
