{{ config(
    materialized='incremental',
    unique_key='pit_stop_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'pitstops') }}
    {% if is_incremental() %}
    where ingested_at > (select max(ingested_at) from {{ this }})
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'driver_id', 'stop']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        "cast(season as varchar)", "cast(round as varchar)", "driver_id", "cast(stop as varchar)"
    ]) }} as pit_stop_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    {{ surrogate_key(["driver_id"]) }} as driver_key,
    season,
    round,
    driver_id,
    stop,
    lap,
    time_of_day,
    duration_millis,
    ingested_at
from ranked
where recency = 1
