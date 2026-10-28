import { ElevationChip } from "@/components/ops/chips";
import { Intro } from "@/components/ops/intro";
import { Contenders, Favourite, Order, Strip, Verdict } from "@/components/ops/panels";
import { Rail } from "@/components/ops/rail";
import { Scene } from "@/components/ops/scene";
import { loadForecast, loadHistory, loadScenery, loadTrack } from "@/lib/load";
import { circuitName } from "@/lib/teams";

const STAGE: Record<string, string> = {
  before_qualifying: "Before qualifying",
  after_qualifying: "After qualifying",
  after_sprint: "After the sprint",
};

export default async function RaceOps() {
  const [forecast, history, track] = await Promise.all([loadForecast(), loadHistory(), loadTrack()]);
  const scenery = await loadScenery(forecast.event.circuit_id);
  const drivers = [...forecast.drivers].sort((a, b) => b.win - a.win);
  const profile = track.profile.circuit_id === forecast.event.circuit_id ? track.profile : null;
  const neighbours = track.neighbours.filter((n) => n.circuit_id !== track.profile.circuit_id).slice(0, 3);
  const stage = history?.snapshots.at(-1)?.stage;
  const credits = scenery ? scenery.sources.join(". ") : "No 3D model of this circuit yet.";

  return (
    <div data-ops-intro className="min-h-dvh">
      {scenery && (
        <Scene circuitId={forecast.event.circuit_id}>
          <h4 className="mb-2 text-sm font-normal text-ink">Start / finish</h4>
          {profile && (
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-xs">
              {[
                ["Length", `${profile.length_km.toFixed(2)} km`],
                ["Corners", String(profile.corners)],
                ["Altitude", `${profile.altitude_m.toLocaleString("en")} m`],
                ["Direction", profile.direction],
              ].map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-mute">{k}</dt>
                  <dd className="figure text-right text-ink">{v}</dd>
                </div>
              ))}
            </dl>
          )}
          {neighbours.length > 0 && (
            <p className="mt-2.5 text-[11px] leading-snug text-mute">
              Most like {neighbours.map((n) => circuitName(n.circuit_id)).join(", ")}
            </p>
          )}
        </Scene>
      )}

      <div className="pointer-events-none relative grid gap-4 p-4 pb-28 lg:fixed lg:inset-0 lg:grid-cols-[minmax(0,1fr)_540px_76px] lg:p-5 lg:pb-[30px]">
        <section className="flex min-h-[calc(56dvh-32px)] min-w-0 flex-col justify-between lg:min-h-0">
          <header className="pointer-events-auto">
            <h1 className="mt-2 mb-3.5 ml-1.5 text-[34px] leading-[1.05] font-extralight tracking-[-0.02em] lg:text-[clamp(36px,4.2vw,60px)]">
              {forecast.event.race_name}
            </h1>
            <div className="ml-1 flex flex-wrap gap-2.5">
              <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">Round {forecast.event.round}</span>
              {stage && <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">{STAGE[stage] ?? stage}</span>}
              {scenery && <ElevationChip />}
            </div>
          </header>
          <div className="pointer-events-auto">
            <Order drivers={drivers} />
          </div>
        </section>

        <section data-ops-panels className="pointer-events-auto flex min-h-0 flex-col gap-3 pb-1 lg:overflow-y-auto">
          <Strip forecast={forecast} />
          {forecast.evidence && <Verdict evidence={forecast.evidence} />}
          <Favourite drivers={drivers} history={history} />
          <Contenders drivers={drivers} />
          <p className="px-1 text-[10px] leading-snug text-faint lg:hidden">{credits}</p>
        </section>

        <Rail startUtc={forecast.event.start_utc} />
      </div>

      <p title={credits} className="fixed bottom-2 left-6 hidden max-w-[calc(100vw-680px)] truncate text-[9.5px] text-faint lg:block">
        {credits}
      </p>
      <div data-ops-title aria-hidden="true" className="fixed inset-0 z-[60] grid place-items-center bg-[#0b0c0e]">
        <span className="display text-[44px] tracking-[0.14em]">PIT ADVISOR</span>
      </div>
      <Intro />
    </div>
  );
}
