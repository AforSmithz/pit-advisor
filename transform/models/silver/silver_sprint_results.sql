{{ config(
    materialized='incremental',
    unique_key='sprint_result_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ optional_source('bronze', 'sprint', "
        select
            cast(null as varchar) as run_id,
            cast(null as timestamp with time zone) as ingested_at,
            cast(null as bigint) as season,
            cast(null as bigint) as round,
            cast(null as varchar) as driver_id,
            cast(null as varchar) as driver_code,
            cast(null as varchar) as constructor_id,
            cast(null as bigint) as car_number,
            cast(null as bigint) as grid,
            cast(null as bigint) as position,
            cast(null as varchar) as position_text,
            cast(null as double) as points,
            cast(null as bigint) as laps_completed,
            cast(null as varchar) as status,
            cast(null as bigint) as time_millis,
            cast(null as bigint) as fastest_lap_rank,
            cast(null as bigint) as fastest_lap_millis
        where false
    ") }}
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
    {{ surrogate_key(["'sprint'", "cast(season as varchar)", "cast(round as varchar)", "driver_id"]) }}
        as sprint_result_id,
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
