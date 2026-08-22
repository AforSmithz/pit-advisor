{{ config(materialized='table') }}

select
    {{ surrogate_key(["constructor_id"]) }} as constructor_key,
    constructor_id,
    min(season) as first_season,
    max(season) as latest_season,
    count(distinct season * 100 + round) as event_count
from {{ source('bronze', 'results') }}
group by constructor_id
