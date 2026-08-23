{{ config(enabled=(var('session_laps') or target.type == 'athena')) }}

-- an incremental model that first built empty stays empty unless the watermark tolerates
-- a null max(), so a silver count below bronze is the watermark filtering everything out
with counted as (
    select
        (select count(*) from {{ source('bronze', 'session_laps') }}) as in_bronze,
        (select count(*) from {{ ref('silver_session_laps') }}) as in_silver
)

select * from counted where in_silver < in_bronze
