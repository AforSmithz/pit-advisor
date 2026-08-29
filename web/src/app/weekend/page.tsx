import { ChapterRing } from "@/components/chapter-ring";
import { Neighbours, Scenario, Share } from "@/components/bars";
import { Engraved, Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { TrackGroup, type Row } from "@/components/track-group";
import { isoDate, label, percentOf } from "@/lib/format";
import { loadWeekend } from "@/lib/load";

export const metadata = { title: "Weekend · Pit Advisor" };

export default async function WeekendPage() {
  const view = await loadWeekend();
  // the lake rates every driver it has ever seen, so the field this season leads and the rest
  // follow under their own rule rather than being dropped
  const drivers = [...view.drivers].sort(
    (a, b) =>
      Number(b.last_season === view.event.season) - Number(a.last_season === view.event.season) ||
      rank(a.form?.value) - rank(b.form?.value),
  );
  const group = (driver: (typeof view.drivers)[number]) =>
    driver.last_season === view.event.season
      ? `the ${view.event.season} field`
      : "raced earlier, still rated";
  const teams = [...view.teams].sort(
    (a, b) => rank(a.track_fit_regression?.value) - rank(b.track_fit_regression?.value),
  );

  const formRows: Row[] = drivers.map((driver) => ({
    name: driver.driver_code,
    sub: label(driver.constructor_id),
    group: group(driver),
    estimate: driver.form,
    missing: driver.form_component === null ? "no shared car lineage" : "no fit",
    href: `/driver/${driver.driver_code}/`,
  }));

  const qualiRows: Row[] = drivers.map((driver) => ({
    name: driver.driver_code,
    sub: label(driver.constructor_id),
    group: group(driver),
    estimate: driver.quali_race,
    missing: "no quali-race pairs",
    href: `/driver/${driver.driver_code}/`,
  }));

  const fitRows: Row[] = teams.map((team) => ({
    name: label(team.constructor_id),
    sub: team.estimators_disagree ? "estimators disagree" : undefined,
    estimate: team.track_fit_regression,
    missing: "no history at this profile",
  }));

  const dnfRows: Row[] = teams.map((team) => ({
    name: label(team.constructor_id),
    estimate: team.dnf_per_lap,
    missing: "no laps in the window",
  }));

  const exclusions = Object.entries(view.coverage.exclusions)
    .map(([name, count]) => ({ name: name.replace(/_/g, " "), count }))
    .sort((a, b) => b.count - a.count);
  const skips = Object.entries(view.coverage.skips)
    .map(([name, count]) => ({ name, count }))
    .sort((a, b) => b.count - a.count);

  return (
    <>
      <ChapterRing
        season={view.event.season}
        round={view.event.round}
        raceName={view.event.race_name}
        circuitId={view.event.circuit_id}
        raceDate={view.event.race_date}
        asOf={view.as_of}
        runId={view.run_id}
        generatedAt={view.generated_at}
      />

      <div className="mt-10 grid gap-10 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-10">
          <Plate
            title="Driver form"
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands={`fitted from ${view.coverage.sessions_fitted} sessions of clean-air race pace`}
              />
            }
            note="Teammate-normalised clean-air race pace, time-decayed. Negative is faster than the reference. Drivers who have not raced this season keep their rating and sit below the field, on the same scale."
          >
            <TrackGroup rows={formRows} unit="% off benchmark" />
          </Plate>

          <Plate
            title="Quali to race"
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands={`fitted from ${view.coverage.quali_events} qualifying events`}
              />
            }
            note="Race pace minus qualifying pace with track evolution removed. Positive means the car comes to them on Sunday."
          >
            <TrackGroup rows={qualiRows} unit="% delta" />
          </Plate>

          <Plate
            title="Track fit"
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands="fitted from every team-race at this circuit profile"
              />
            }
            note="Two estimators on the same question: a ridge on circuit demands, and a similarity-weighted mean over neighbouring circuits."
          >
            <TrackGroup rows={fitRows} unit="% off benchmark" />
          </Plate>

          <Plate
            title="Reliability"
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands="fitted from every racing lap in the decay window"
              />
            }
            note="Pooled retirement hazard per racing lap. Cause coverage is thin, so only the pooled figure is published here."
          >
            <TrackGroup rows={dnfRows} unit="per lap" digits={4} signedValue={false} />
          </Plate>
        </div>

        <aside className="flex flex-col gap-10">
          <Plate title="Weather scenario">
            {view.weather ? (
              <>
                <Scenario dry={view.weather.dry} mixed={view.weather.mixed} wet={view.weather.wet} />
                <dl className="mt-5">
                  <Engraved term="hours covered">{view.weather.hours}</Engraved>
                  <Engraved term="expected rain">{view.weather.expected_mm.toFixed(2)} mm</Engraved>
                  <Engraved term="wettest hour">
                    {view.weather.wettest_hour.toFixed(2)} mm/h
                  </Engraved>
                  <Engraved term="source">
                    {view.weather.is_forecast ? "forecast" : "archive"}
                  </Engraved>
                </dl>
                {!view.weather.is_forecast && (
                  <p className="engraved mt-4 normal-case tracking-normal text-lume-dim">
                    An archive hour carries no probability, so this split is a hard nought or one
                    rather than a hedge. The weights do not yet beat an always-dry constant.
                  </p>
                )}
              </>
            ) : (
              <div className="cut-face p-4">
                <span className="engraved text-lume-dim">no weather rows for this event</span>
              </div>
            )}
          </Plate>

          <Plate title="Nearest circuits">
            <Neighbours rows={view.neighbours.slice(0, 6)} />
          </Plate>

          <Plate title="What this stands on">
            <dl className="mb-6">
              <Engraved term="races in lake">{view.coverage.races_in_lake}</Engraved>
              <Engraved term="sessions fitted">{view.coverage.sessions_fitted}</Engraved>
              <Engraved term="drivers rated">{view.coverage.drivers_rated}</Engraved>
              <Engraved term="quali events">{view.coverage.quali_events}</Engraved>
              <Engraved term="clean laps">
                {view.coverage.clean_laps.toLocaleString("en-GB")}
              </Engraved>
              <Engraved term="lap exclusion rate">
                {percentOf(view.coverage.exclusion_rate)}
              </Engraved>
              <Engraved term="cause coverage">{percentOf(view.cause_coverage)}</Engraved>
              <Engraved term="as of">{isoDate(view.as_of)}</Engraved>
            </dl>
            <h3 className="engraved mb-2 text-lume">Laps excluded</h3>
            <Share rows={exclusions} total={view.coverage.total_laps} />
            <h3 className="engraved mt-6 mb-2 text-lume">Sessions with no fit</h3>
            <Share rows={skips} total={view.coverage.sessions_skipped} />
          </Plate>
        </aside>
      </div>
    </>
  );
}

function rank(value: number | undefined): number {
  return value === undefined ? Number.POSITIVE_INFINITY : value;
}
