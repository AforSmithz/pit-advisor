{{ config(
    materialized='incremental',
    unique_key='result_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'results') }}
    {% if is_incremental() %}
    {{ newer_than_loaded() }}
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'driver_id']) }} as recency
    from source_rows

)

select
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)", "driver_id"]) }}
        as result_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    {{ surrogate_key(["driver_id"]) }} as driver_key,
    {{ surrogate_key(["constructor_id"]) }} as constructor_key,
    season,
    round,
    driver_id,
    constructor_id,
    car_number,
    grid,
    position,
    position_text,
    points,
    laps_completed,
    status,
    time_millis,
    fastest_lap_rank,
    fastest_lap_millis,
    -- a pit lane start is published as grid 0, which is not a grid slot
    case when grid = 0 then true else false end as started_from_pit,
    case when position is null then false else true end as is_classified,
    case
        when lower(status) = 'finished' then 'finished'
        when lower(status) like '+%lap%' then 'finished'
        when lower(status) = 'disqualified' then 'disqualified'
        when lower(status) like 'did not%' then 'did_not_start'
        when lower(status) = 'withdrew' then 'did_not_start'
        else 'retired'
    end as status_class,
    case when position is null or grid = 0 then null else grid - position end as positions_gained,
    ingested_at
from ranked
where recency = 1
