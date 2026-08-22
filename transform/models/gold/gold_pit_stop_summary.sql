{{ config(materialized='table', partitioned_by=['season']) }}

with stops as (

    select * from {{ ref('silver_pit_stops') }}

),

events as (

    select * from {{ ref('silver_events') }}

),

per_driver as (

    select
        event_id,
        driver_key,
        driver_id,
        count(*) as stop_count,
        min(lap) as first_stop_lap,
        max(lap) as last_stop_lap,
        sum(duration_millis) as total_duration_millis,
        avg(duration_millis) as avg_duration_millis,
        min(duration_millis) as best_duration_millis,
        count(duration_millis) as timed_stop_count,
        max(ingested_at) as ingested_at
    from stops
    group by event_id, driver_key, driver_id

),

per_event as (

    select event_id, avg(duration_millis) as event_avg_duration_millis
    from stops
    group by event_id

)

select
    per_driver.event_id,
    events.season,
    events.round,
    events.race_name,
    events.circuit_id,
    events.race_date,
    per_driver.driver_key,
    per_driver.driver_id,
    per_driver.stop_count,
    per_driver.timed_stop_count,
    per_driver.first_stop_lap,
    per_driver.last_stop_lap,
    per_driver.total_duration_millis,
    per_driver.avg_duration_millis,
    per_driver.best_duration_millis,
    per_event.event_avg_duration_millis,
    per_driver.avg_duration_millis - per_event.event_avg_duration_millis as delta_to_event_avg,
    per_driver.ingested_at
from per_driver
inner join events on per_driver.event_id = events.event_id
inner join per_event on per_driver.event_id = per_event.event_id
