import { notFound } from "next/navigation";
import { ChapterRing } from "@/components/chapter-ring";
import { Neighbours } from "@/components/bars";
import { Engraved, Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { Trace } from "@/components/trace";
import { TrackGroup, type Row } from "@/components/track-group";
import { label } from "@/lib/format";
import { loadTrack } from "@/lib/load";

export async function generateStaticParams() {
  const view = await loadTrack();
  return [{ circuit: view.profile.circuit_id }];
}

const DEMANDS = ["downforce", "traction", "braking", "top_speed", "abrasion", "kerbs"];

export default async function TrackPage({ params }: { params: Promise<{ circuit: string }> }) {
  const { circuit } = await params;
  const view = await loadTrack();
  if (circuit !== view.profile.circuit_id) notFound();

  const teams = [...view.teams].sort(
    (a, b) => (a.regression?.value ?? Infinity) - (b.regression?.value ?? Infinity),
  );

  const regressionRows: Row[] = teams.map((team) => ({
    name: label(team.constructor_id),
    sub: team.disagree ? "estimators disagree" : undefined,
    estimate: team.regression,
    missing: "no fit on this profile",
  }));

  const similarityRows: Row[] = teams.map((team) => ({
    name: label(team.constructor_id),
    estimate: team.similarity,
    missing: "no neighbouring history",
  }));

  const history = teams.flatMap((team) =>
    team.history.map((sample) => ({
      key: `${team.constructor_id}-${sample.season}-${sample.round}`,
      date: sample.race_date,
      value: sample.percent_off_benchmark,
      wet: sample.regime !== "dry",
      circuit: sample.circuit_id,
    })),
  );

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
            title="Fitted on circuit demands"
            note="A ridge over the hand-maintained circuit taxonomy: what the car should do here given what it has done at circuits that ask for the same things."
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands={`${view.teams.length} teams, ${view.dropped_reprofiled} races dropped as reprofiled`}
              />
            }
          >
            <TrackGroup rows={regressionRows} unit="% off benchmark" />
          </Plate>

          <Plate
            title="Weighted by similar circuits"
            note="The same question answered a second way, as a similarity-weighted mean over neighbouring circuits. Where the two disagree, neither is trusted."
          >
            <TrackGroup rows={similarityRows} unit="% off benchmark" />
          </Plate>

          <Plate
            title="Everything ever run here"
            note="Every fitted team-race at this circuit, hollow rings for wet races."
          >
            <Trace
              caption={`${history.length} team-races at ${label(view.profile.circuit_id)}.`}
              fastEnd="faster"
              slowEnd="slower"
              points={history}
            />
          </Plate>
        </div>

        <aside className="flex flex-col gap-10">
          <Plate title="Circuit">
            <dl>
              <Engraved term="length">{view.profile.length_km.toFixed(3)} km</Engraved>
              <Engraved term="corners">{view.profile.corners}</Engraved>
              <Engraved term="direction">{view.profile.direction}</Engraved>
              <Engraved term="altitude">{view.profile.altitude_m} m</Engraved>
              <Engraved term="reprofiled">{view.profile.reprofiled ?? "never"}</Engraved>
              <Engraved term="races dropped">{view.dropped_reprofiled}</Engraved>
            </dl>
            <p className="engraved mt-5 normal-case tracking-normal text-lume-dim">
              Races run before a reprofiling are dropped rather than blended: the circuit that
              produced them no longer exists.
            </p>
          </Plate>

          <Plate title="Demands" note="Hand-maintained taxonomy, one to five.">
            <ul>
              {DEMANDS.map((demand) => {
                const value = view.profile.demand[demand] ?? 0;
                return (
                  <li key={demand} className="border-t border-engrave py-2">
                    <div className="mb-1 flex items-baseline justify-between">
                      <span className="engraved">{demand.replace(/_/g, " ")}</span>
                      <span className="figure text-sm text-lume">{value}</span>
                    </div>
                    <div className="flex gap-1" aria-hidden>
                      {[1, 2, 3, 4, 5].map((step) => (
                        <span
                          key={step}
                          className={`h-1 flex-1 ${step <= value ? "bg-steel" : "bg-engrave"}`}
                        />
                      ))}
                    </div>
                  </li>
                );
              })}
            </ul>
          </Plate>

          <Plate title="Nearest circuits">
            <Neighbours rows={view.neighbours.slice(0, 8)} />
          </Plate>
        </aside>
      </div>
    </>
  );
}
