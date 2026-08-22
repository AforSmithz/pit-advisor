select event_id, count(*) as winners
from {{ ref('gold_race_results') }}
where is_winner
group by event_id
having count(*) <> 1
