{{ config(
    materialized='incremental',
    unique_key='lap_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'laps') }}
    {% if is_incremental() %}
    where ingested_at > (select max(ingested_at) from {{ this }})
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'driver_id', 'lap']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        "cast(season as varchar)", "cast(round as varchar)", "driver_id", "cast(lap as varchar)"
    ]) }} as lap_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    {{ surrogate_key(["driver_id"]) }} as driver_key,
    season,
    round,
    driver_id,
    lap,
    position,
    time_millis,
    ingested_at
from ranked
where recency = 1
