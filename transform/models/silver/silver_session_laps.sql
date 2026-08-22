{{ config(
    materialized='incremental',
    unique_key='session_lap_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
    enabled=(var('session_laps') or target.type == 'athena'),
) }}

with source_rows as (

    select * from {{ source('bronze', 'session_laps') }}
    {% if is_incremental() %}
    where ingested_at > (select max(ingested_at) from {{ this }})
    {% endif %}

),

ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'session', 'driver_code', 'lap']) }} as recency
    from source_rows

)

select
    {{ surrogate_key([
        "cast(season as varchar)",
        "cast(round as varchar)",
        "session",
        "driver_code",
        "cast(lap as varchar)",
    ]) }} as session_lap_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    season,
    round,
    session,
    driver_code,
    driver_number,
    lap,
    lap_time_millis,
    sector1_millis,
    sector2_millis,
    sector3_millis,
    stint,
    lap_in_stint,
    compound,
    tyre_life,
    is_personal_best,
    is_deleted,
    is_accurate,
    track_status,
    pit_in,
    pit_out,
    position,
    -- fastf1 concatenates one digit per marshal sector, so a 4 anywhere means the lap saw a sc
    case when track_status like '%4%' then true else false end as saw_safety_car,
    case when track_status like '%6%' or track_status like '%7%' then true else false end
        as saw_virtual_safety_car,
    case when track_status like '%5%' then true else false end as saw_red_flag,
    ingested_at
from ranked
where recency = 1
