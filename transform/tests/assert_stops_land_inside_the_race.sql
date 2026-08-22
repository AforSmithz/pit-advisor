-- a disqualification zeroes laps_completed while the stops it made stay published,
-- so only classified drivers can be checked against their own lap count
select stops.pit_stop_id
from {{ ref('silver_pit_stops') }} as stops
inner join {{ ref('silver_results') }} as results
    on stops.event_id = results.event_id and stops.driver_id = results.driver_id
where results.status_class in ('finished', 'retired')
  and stops.lap > results.laps_completed
