{{ config(materialized='table') }}

with ranked as (

    select
        *,
        {{ latest_by(['season', 'round', 'circuit_id', 'observed_at', 'is_forecast']) }} as recency
    from {{ source('bronze', 'weather') }}

)

select
    {{ surrogate_key([
        "cast(season as varchar)",
        "cast(round as varchar)",
        "circuit_id",
        "cast(observed_at as varchar)",
        "cast(is_forecast as varchar)",
    ]) }} as observation_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    season,
    round,
    circuit_id,
    observed_at,
    is_forecast,
    temperature_c,
    precipitation_mm,
    precipitation_probability,
    wind_speed_kph,
    relative_humidity,
    case when precipitation_mm >= 0.1 then true else false end as is_wet,
    ingested_at
from ranked
where recency = 1
