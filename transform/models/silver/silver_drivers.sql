{{ config(materialized='table') }}

with appearances as (

    select season, round, driver_id, constructor_id, ingested_at
    from {{ source('bronze', 'results') }}

    union all

    select season, round, driver_id, constructor_id, ingested_at
    from {{ source('bronze', 'qualifying') }}

),

ranked as (

    select
        *,
        {{ latest_by(['driver_id'], 'season * 100 + round') }} as recency
    from appearances

)

select
    {{ surrogate_key(["driver_id"]) }} as driver_key,
    driver_id,
    max(case when recency = 1 then constructor_id end) as latest_constructor_id,
    min(season) as first_season,
    max(season) as latest_season,
    count(distinct season * 100 + round) as event_count
from ranked
group by driver_id
