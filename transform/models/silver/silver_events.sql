{{ config(materialized='table') }}

with ranked as (

    select
        *,
        {{ latest_by(['season', 'round']) }} as recency
    from {{ source('bronze', 'races') }}

)

select
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    season,
    round,
    race_name,
    circuit_id,
    circuit_name,
    locality,
    country,
    latitude,
    longitude,
    race_date,
    start_utc,
    ingested_at
from ranked
where recency = 1
