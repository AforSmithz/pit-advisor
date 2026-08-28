import { ChapterRing } from "@/components/chapter-ring";
import { Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { TrackGroup, type Row } from "@/components/track-group";
import { label } from "@/lib/format";
import { loadDriver } from "@/lib/load";

export default async function DriverIndex() {
  const view = await loadDriver();
  const drivers = [...view.drivers].sort(
    (a, b) => (a.form?.value ?? Infinity) - (b.form?.value ?? Infinity),
  );

  const rows: Row[] = drivers.map((driver) => ({
    name: driver.driver_code,
    sub: `${label(driver.constructor_id)} · ${driver.pace.length} rated races`,
    estimate: driver.form,
    missing: driver.form_component === null ? "no shared car lineage" : "no fit",
    href: `/driver/${driver.driver_code}/`,
  }));

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
      <div className="mt-10">
        <Plate
          title="Every rated driver"
          note={`Form is a teammate-normalised effect with a ${view.half_life_events}-event half life. A driver with no shared car lineage has no comparable figure at all.`}
          footer={
            <Provenance
              view={view.view}
              runId={view.run_id}
              asOf={view.as_of}
              stands={`${view.drivers.length} drivers rated`}
            />
          }
        >
          <TrackGroup rows={rows} unit="% off benchmark" />
        </Plate>
      </div>
    </>
  );
}
