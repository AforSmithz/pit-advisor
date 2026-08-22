{{ config(
    materialized='incremental',
    unique_key='qualifying_id',
    incremental_strategy=merge_or_replace(),
    partitioned_by=['season'],
) }}

with source_rows as (

    select * from {{ source('bronze', 'qualifying') }}
    {% if is_incremental() %}
    where ingested_at > (select max(ingested_at) from {{ this }})
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
        as qualifying_id,
    {{ surrogate_key(["cast(season as varchar)", "cast(round as varchar)"]) }} as event_id,
    {{ surrogate_key(["driver_id"]) }} as driver_key,
    {{ surrogate_key(["constructor_id"]) }} as constructor_key,
    season,
    round,
    driver_id,
    constructor_id,
    position,
    q1_millis,
    q2_millis,
    q3_millis,
    coalesce(q3_millis, q2_millis, q1_millis) as best_millis,
    case
        when q3_millis is not null then 3
        when q2_millis is not null then 2
        when q1_millis is not null then 1
    end as segments_reached,
    ingested_at
from ranked
where recency = 1
