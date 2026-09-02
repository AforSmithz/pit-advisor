{{ config(
    materialized='incremental',
    unique_key='sanction_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'incident_sanctions') }}
    {% if is_incremental() %}
    {{ newer_than_loaded() }}
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'document', 'entry', 'ordinal']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        as_text('season'), as_text('round'), as_text('document'),
        as_text('entry'), as_text('ordinal')
    ]) }} as sanction_id,
    {{ surrogate_key([
        as_text('season'), as_text('round'), as_text('document'), as_text('entry')
    ]) }} as incident_id,
    season,
    round,
    document,
    entry,
    ordinal,
    kind,
    seconds,
    positions,
    points,
    points_total,
    amount,
    currency,
    text,
    ingested_at
from ranked
where recency = 1
