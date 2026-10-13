import Link from "next/link";
import { Board } from "@/components/race/board";
import { Drift } from "@/components/race/drift";
import { PageHead } from "@/components/tile";
import { BASELINES, snapshotLabel, stamp } from "@/lib/format";
import { loadForecast, loadHistory, loadSprint, loadStewards } from "@/lib/load";
import type { ForecastView } from "@/lib/schemas";
import { circuitName } from "@/lib/teams";

const STAGE: Record<string, string> = {
  before_qualifying: "before qualifying",
  after_qualifying: "after qualifying",
  after_sprint: "after the sprint",
};

function Trust({ evidence }: { evidence: NonNullable<ForecastView["evidence"]> }) {
  const baselines = Object.keys(BASELINES).filter((name) => name in evidence.log_loss);
  const beaten = baselines.filter((name) => evidence.separated_from.includes(name));
  const tied = baselines.filter((name) => !evidence.separated_from.includes(name));
  const list = (names: string[]) => names.map((name) => BASELINES[name]).join(" and ");
  return (
    <Link href="/record/" className="inset press flex items-center gap-3.5 rounded-[18px] px-4 py-3">
      <span className="flex flex-1 flex-col gap-1.5">
        <span className="text-xs text-ink-3">Tested on {evidence.races} past races</span>
        <span className="flex gap-1.5">
          {baselines.map((name) => {
            const separated = evidence.separated_from.includes(name);
            return (
              <span
                key={name}
                title={BASELINES[name]}
                className="h-1.5 flex-1 rounded-full"
                style={
                  separated
                    ? { background: "var(--warm)", boxShadow: "0 0 8px rgb(255 95 109 / 0.6)" }
                    : { background: "#3a3c42" }
                }
              />
            );
          })}
        </span>
        <span className="text-[11px] text-mute">
          {beaten.length > 0 ? `clearly better than ${list(beaten)}` : "not clearly better than any simple guess"}
          {tied.length > 0 && beaten.length > 0 ? `, level with ${list(tied)}` : ""}
        </span>
      </span>
      <span aria-hidden="true" className="text-lg text-signal">
        →
      </span>
    </Link>
  );
}

export default async function RaceWeek() {
  const [forecast, history, sprint, stewards] = await Promise.all([
    loadForecast(),
    loadHistory(),
    loadSprint(),
    loadStewards(),
  ]);
  const snapshots = history?.snapshots.filter((snapshot) => !snapshot.repeat) ?? [];
  const current = history?.snapshots.find((snapshot) => snapshot.run_id === forecast.run_id);
  const drops = (stewards?.rulings ?? []).filter(
    (ruling) =>
      ruling.sanction.outcome === "grid" &&
      forecast.drivers.some((driver) => driver.driver_code === ruling.driver_code),
  );
  const stage = current ? STAGE[current.stage] : undefined;

  return (
    <>
      <PageHead
        title={forecast.event.race_name}
        sub={`${circuitName(forecast.event.circuit_id)}, ${forecast.laps} laps. Forecast ${stage ? `published ${stage}, ` : "published "}${stamp(forecast.generated_at)}.`}
      >
        <div className="flex flex-wrap items-center gap-2.5">
          {drops.map((ruling) => {
            const grid = forecast.drivers.find((driver) => driver.driver_code === ruling.driver_code)?.grid;
            return (
              <Link
                key={`${ruling.document}-${ruling.entry}`}
                href="/stewards/"
                className="glass press flex items-center gap-2.5 rounded-full border-magenta/40 px-3.5 py-2 text-[12.5px]"
              >
                <span className="h-2 w-2 rounded-full bg-magenta shadow-[0_0_8px_#c03fd0]" />
                {ruling.driver_code}: {ruling.sanction.text.toLowerCase()}
                {grid ? `. The forecast starts ${ruling.driver_code} from P${grid}` : ""}
              </Link>
            );
          })}
          {snapshots.length > 0 && (
            <ol aria-label="Forecasts published this week" className="inset-sm flex items-center gap-0.5 rounded-full px-2 py-1.5">
              {snapshots.map((snapshot) => {
                const now = snapshot.run_id === forecast.run_id;
                return (
                  <li key={snapshot.run_id} className="flex flex-col items-center gap-[3px] px-[7px]">
                    <span
                      className="h-[9px] w-[9px] rounded-full"
                      style={
                        now
                          ? { background: "#7ea2ff", boxShadow: "0 0 10px rgb(126 162 255 / 0.9)" }
                          : { background: "#5c5e66" }
                      }
                    />
                    <span className={`text-[10px] whitespace-nowrap ${now ? "text-ink" : "text-mute"}`}>
                      {snapshot.stage === "after_qualifying" ? "Quali" : snapshotLabel(snapshot).slice(0, 3)}
                    </span>
                  </li>
                );
              })}
              <li className="flex flex-col items-center gap-[3px] px-[7px]">
                <span className="h-[9px] w-[9px] rounded-full shadow-[inset_0_0_0_1.5px_#5c5e66]" />
                <span className="text-[10px] text-mute">Race</span>
              </li>
            </ol>
          )}
        </div>
      </PageHead>
      <Board
        race={forecast}
        sprint={sprint?.state === "forecast" ? sprint.forecast : null}
        trust={forecast.evidence ? <Trust evidence={forecast.evidence} /> : null}
        drift={<Drift snapshots={history?.snapshots ?? []} start={history?.event.start_utc ?? null} />}
      />
    </>
  );
}
