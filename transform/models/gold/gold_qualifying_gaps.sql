{{ config(materialized='table', partitioned_by=['season']) }}

with qualifying as (

    select * from {{ ref('silver_qualifying') }}
    where best_millis is not null

),

events as (

    select * from {{ ref('silver_events') }}

),

pole as (

    select event_id, min(best_millis) as pole_millis
    from qualifying
    group by event_id

),

teammates as (

    select
        mine.qualifying_id,
        min(theirs.best_millis) as teammate_best_millis
    from qualifying as mine
    inner join qualifying as theirs
        on mine.event_id = theirs.event_id
        and mine.constructor_id = theirs.constructor_id
        and mine.driver_id <> theirs.driver_id
    group by mine.qualifying_id

)

select
    qualifying.qualifying_id,
    qualifying.event_id,
    events.season,
    events.round,
    events.race_name,
    events.circuit_id,
    events.race_date,
    qualifying.driver_key,
    qualifying.driver_id,
    qualifying.constructor_key,
    qualifying.constructor_id,
    qualifying.position,
    qualifying.segments_reached,
    qualifying.best_millis,
    pole.pole_millis,
    qualifying.best_millis - pole.pole_millis as gap_to_pole_millis,
    -- percent of pole, the only form that compares across circuits of different lap length
    100.0 * (qualifying.best_millis - pole.pole_millis) / pole.pole_millis as gap_to_pole_pct,
    teammates.teammate_best_millis,
    qualifying.best_millis - teammates.teammate_best_millis as gap_to_teammate_millis,
    qualifying.ingested_at
from qualifying
inner join events on qualifying.event_id = events.event_id
inner join pole on qualifying.event_id = pole.event_id
left join teammates on qualifying.qualifying_id = teammates.qualifying_id
