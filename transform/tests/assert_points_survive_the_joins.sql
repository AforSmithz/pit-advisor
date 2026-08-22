with silver as (
    select event_id, sum(points) as points from {{ ref('silver_results') }} group by event_id
),

gold as (
    select event_id, sum(points) as points from {{ ref('gold_race_results') }} group by event_id
)

select silver.event_id
from silver
full outer join gold on silver.event_id = gold.event_id
where silver.points is distinct from gold.points
