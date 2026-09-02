{{ config(materialized='table', partitioned_by=['season']) }}

-- One row per driver ruling, carrying the rule cited and the sanction imposed, so a question
-- of the form "what have the stewards done before for this offence" is a group by rather than
-- a search. Stewards' decisions are not binding precedent and this table does not imply they
-- are: it counts what happened, nothing more.

with incidents as (

    select * from {{ ref('silver_incidents') }}

),

events as (

    select * from {{ ref('silver_events') }}

),

sanctions as (

    select * from {{ ref('silver_incident_sanctions') }}

),

articles as (

    select * from {{ ref('silver_incident_articles') }}

),

primary_article as (

    -- a charge cites more than one rule; the first is the one the decision is filed under
    select incident_id, book, code, edition
    from (
        select
            *,
            row_number() over (partition by incident_id order by book, code) as ordering
        from articles
    ) as ranked
    where ordering = 1

)

select
    incidents.incident_id,
    incidents.event_id,
    incidents.season,
    incidents.round,
    events.race_name,
    events.circuit_id,
    events.race_date,
    incidents.document_name,
    incidents.document,
    incidents.entry,
    incidents.kind,
    incidents.issued,
    incidents.session,
    incidents.car,
    incidents.driver,
    incidents.competitor,
    incidents.fact,
    incidents.charge,
    incidents.outcome,
    incidents.reason,
    primary_article.book as rule_book,
    primary_article.code as rule_code,
    primary_article.edition as rule_edition,
    sanctions.ordinal as sanction_ordinal,
    sanctions.kind as sanction_kind,
    sanctions.seconds as sanction_seconds,
    sanctions.positions as sanction_positions,
    sanctions.points as sanction_points,
    sanctions.amount as sanction_amount,
    sanctions.currency as sanction_currency,
    incidents.read_by,
    incidents.unverified_fields,
    incidents.raw_key,
    incidents.ingested_at
from incidents
inner join events on incidents.event_id = events.event_id
left join primary_article on incidents.incident_id = primary_article.incident_id
left join sanctions on incidents.incident_id = sanctions.incident_id
