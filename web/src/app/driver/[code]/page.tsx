import { notFound } from "next/navigation";
import { ChapterRing } from "@/components/chapter-ring";
import { Engraved, Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { Trace } from "@/components/trace";
import { TrackGroup, type Row } from "@/components/track-group";
import { label } from "@/lib/format";
import { loadDriver } from "@/lib/load";

export async function generateStaticParams() {
  const view = await loadDriver();
  return view.drivers.map((driver) => ({ code: driver.driver_code }));
}

export default async function DriverPage({ params }: { params: Promise<{ code: string }> }) {
  const { code } = await params;
  const view = await loadDriver();
  const driver = view.drivers.find((item) => item.driver_code === code);
  if (!driver) notFound();

  const rows: Row[] = [
    {
      name: "Form",
      sub: "teammate-normalised",
      estimate: driver.form,
      missing: driver.form_component === null ? "no shared car lineage" : "no fit",
    },
    {
      name: "Quali to race",
      sub: "evolution removed",
      estimate: driver.quali_race,
      missing: "no quali-race pairs",
    },
    { name: "Wet delta", sub: "wet minus dry", estimate: driver.wet, missing: "no wet sessions" },
  ];

  const teammates = [...new Set(driver.teammate.map((sample) => sample.teammate))];

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

      <div className="mt-10 flex items-baseline gap-4">
        <h2 className="legend text-6xl text-lume">{driver.driver_code}</h2>
        <p className="engraved">{label(driver.constructor_id)}</p>
      </div>

      <div className="mt-8 grid gap-10 xl:grid-cols-[minmax(0,1fr)_18rem]">
        <div className="flex flex-col gap-10">
          <Plate
            title="Where he stands"
            footer={
              <Provenance
                view={view.view}
                runId={view.run_id}
                asOf={view.as_of}
                stands={`fitted from ${driver.pace.length} races and ${driver.teammate.length} teammate pairs`}
              />
            }
          >
            <TrackGroup rows={rows} unit="% off benchmark" />
          </Plate>

          <Plate
            title="Clean-air race pace"
            note="One mark per fitted race, as a percentage off the session benchmark. Hollow rings are wet races, which are fitted separately."
          >
            <Trace
              caption={`${driver.pace.length} fitted races.`}
              fastEnd="faster"
              slowEnd="slower"
              points={driver.pace.map((sample) => ({
                key: `${sample.season}-${sample.round}-${sample.circuit_id}`,
                date: sample.race_date,
                value: sample.percent_off_benchmark,
                wet: sample.regime !== "dry",
                circuit: sample.circuit_id,
              }))}
            />
          </Plate>

          <Plate
            title="Against the other side of the garage"
            note="Raw teammate deltas, the input the form effect is fitted from. Negative means he was ahead."
          >
            <Trace
              caption={
                teammates.length
                  ? `${driver.teammate.length} paired races against ${teammates.join(", ")}.`
                  : "No paired races: this driver never shared a car with a rated teammate."
              }
              fastEnd="ahead"
              slowEnd="behind"
              points={driver.teammate.map((sample) => ({
                key: `${sample.season}-${sample.round}-${sample.teammate}`,
                date: sample.race_date,
                value: sample.delta,
                wet: false,
                circuit: sample.circuit_id,
              }))}
            />
          </Plate>
        </div>

        <aside>
          <Plate title="Sample">
            <dl>
              <Engraved term="fitted races">{driver.pace.length}</Engraved>
              <Engraved term="paired races">{driver.teammate.length}</Engraved>
              <Engraved term="teammates">{teammates.length}</Engraved>
              <Engraved term="form samples">{driver.form?.samples ?? 0}</Engraved>
              <Engraved term="half life">{view.half_life_events} events</Engraved>
              <Engraved term="lineage">
                {driver.form_component === null ? "unconnected" : `component ${driver.form_component}`}
              </Engraved>
            </dl>
            <p className="engraved mt-5 normal-case tracking-normal text-lume-dim">
              Form is only comparable inside one lineage of shared cars. Two drivers in different
              components can both be rated and still not be comparable to each other.
            </p>
          </Plate>
        </aside>
      </div>
    </>
  );
}
