{{ config(materialized='table', partitioned_by=['season']) }}

with results as (

    select * from {{ ref('silver_results') }}

),

events as (

    select * from {{ ref('silver_events') }}

),

field as (

    select
        event_id,
        count(*) as field_size,
        max(case when position = 1 then driver_id end) as winner_driver_id
    from results
    group by event_id

)

select
    results.result_id,
    results.event_id,
    events.season,
    events.round,
    events.race_name,
    events.circuit_id,
    events.race_date,
    results.driver_key,
    results.driver_id,
    results.constructor_key,
    results.constructor_id,
    results.grid,
    results.started_from_pit,
    results.position,
    results.position_text,
    results.points,
    results.laps_completed,
    results.status,
    results.status_class,
    results.is_classified,
    results.positions_gained,
    results.fastest_lap_rank,
    results.fastest_lap_millis,
    field.field_size,
    case when results.position = 1 then true else false end as is_winner,
    case when results.position <= 3 then true else false end as is_podium,
    case when results.position <= 10 then true else false end as is_points_finish,
    results.ingested_at
from results
inner join events on results.event_id = events.event_id
inner join field on results.event_id = field.event_id
