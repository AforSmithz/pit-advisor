import { Rail } from "@/components/ops/rail";
import { Scene } from "@/components/ops/scene";
import { loadForecast, loadScenery, loadTrack } from "@/lib/load";
import { circuitName } from "@/lib/teams";

export default async function OpsLayout({ children }: { children: React.ReactNode }) {
  const [forecast, track] = await Promise.all([loadForecast(), loadTrack()]);
  const scenery = await loadScenery(forecast.event.circuit_id);
  const profile = track.profile.circuit_id === forecast.event.circuit_id ? track.profile : null;
  const neighbours = track.neighbours.filter((n) => n.circuit_id !== track.profile.circuit_id).slice(0, 3);
  const credits = scenery ? scenery.sources.join(". ") : null;

  return (
    <>
      {scenery ? (
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
      ) : (
        <div aria-hidden="true" className="pointer-events-none fixed inset-0 overflow-hidden">
          <div className="absolute -top-60 left-44 h-[620px] w-[820px] rounded-full bg-[radial-gradient(closest-side,rgba(79,125,255,0.18),transparent)]" />
          <div className="absolute top-72 -right-52 h-[700px] w-[760px] rounded-full bg-[radial-gradient(closest-side,rgba(255,95,109,0.13),transparent)]" />
        </div>
      )}
      {/* painted before the pages, so a pane scrolling over it covers it rather than the other way round */}
      {credits && (
        <p title={credits} className="fixed bottom-2 left-6 hidden max-w-[60vw] truncate text-[9.5px] text-faint lg:block">
          {credits}
        </p>
      )}
      {children}
      {credits && <p className="relative px-5 pb-28 text-[10px] leading-snug text-faint lg:hidden">{credits}</p>}
      <Rail startUtc={forecast.event.start_utc} />
      <div data-ops-title aria-hidden="true" className="fixed inset-0 z-[60] grid place-items-center bg-[#0b0c0e]">
        <span className="display text-[44px] tracking-[0.14em]">PIT ADVISOR</span>
      </div>
    </>
  );
}
