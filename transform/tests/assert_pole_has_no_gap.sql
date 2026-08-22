select qualifying_id, gap_to_pole_millis
from {{ ref('gold_qualifying_gaps') }}
where best_millis = pole_millis and gap_to_pole_millis <> 0
