import { Frame } from "@/components/ops/frame";
import { PageHead } from "@/components/tile";
import { plain, signed } from "@/lib/format";
import { along, polar } from "@/lib/geometry";
import { loadForecast, loadTrack } from "@/lib/load";
import type { Estimate } from "@/lib/schemas";
import { circuitName, teamColor, teamName } from "@/lib/teams";

export const metadata = { title: "Circuits · Pit Advisor" };

const DEMANDS = [
  ["downforce", "Downforce"],
  ["traction", "Traction"],
  ["braking", "Braking"],
  ["kerbs", "Kerbs"],
  ["abrasion", "Abrasion"],
  ["top_speed", "Top speed"],
] as const;

const PIPS = ["#8a3dff", "#c03fd0", "#ff5f6d", "#ff9a3c", "#ffd25a"];
const NODES = ["#ff5f6d", "#ff9a3c", "#c03fd0", "#8a5cff", "#3a8bff", "#2ee6c5", "#58c4ff"];

function Interval({
  estimate,
  x,
  top,
  fill,
  mark,
}: {
  estimate: Estimate | null;
  x: (value: number) => number;
  top: number;
  fill: string;
  mark: string;
}) {
  if (!estimate) return null;
  return (
    <>
      <span
        className="grow absolute h-1.5 rounded-full"
        style={{ top, left: `${x(estimate.low)}%`, width: `${x(estimate.high) - x(estimate.low)}%`, background: fill }}
      />
      <span
        className="absolute h-2.5 w-[3px] -translate-x-1/2 rounded-sm"
        style={{ top: top - 2, left: `${x(estimate.value)}%`, background: mark }}
      />
    </>
  );
}

export default async function Circuits() {
  const [track, forecast] = await Promise.all([loadTrack(), loadForecast()]);
  const profile = track.profile;
  const name = circuitName(profile.circuit_id);
  const racing = new Set(forecast.drivers.map((driver) => driver.constructor_id));
  const teams = track.teams
    .filter((team) => racing.has(team.constructor_id))
    .sort((a, b) => (a.regression?.value ?? 99) - (b.regression?.value ?? 99));
  const bounds = teams.flatMap((team) =>
    [team.regression, team.similarity].flatMap((estimate) => (estimate ? [estimate.low, estimate.high] : [])),
  );
  const lo = Math.floor(Math.min(0, ...bounds));
  const hi = Math.ceil(Math.max(...bounds));
  const x = (value: number) => along(value, lo, hi) * 100;
  const scale = Array.from({ length: hi - lo + 1 }, (_, i) => lo + i);
  const neighbours = track.neighbours.filter((item) => item.circuit_id !== profile.circuit_id);
  const nodes = neighbours.map((item, i) => {
    const angle = -40 + (i * 360) / Math.max(neighbours.length, 1);
    const [nx, ny] = polar(280, 280, (1 - item.similarity) * 300, angle);
    const right = nx >= 280;
    return { ...item, nx, ny, right, color: NODES[i % NODES.length]! };
  });
  const facts = [
    ["Length", `${plain(profile.length_km)} km`, true],
    ["Corners", String(profile.corners), true],
    ["Direction", profile.direction.charAt(0).toUpperCase() + profile.direction.slice(1), false],
    ["Altitude", `${Math.round(profile.altitude_m)} m`, true],
  ] as const;

  return (
    <Frame
      head={
        <PageHead title="Circuits" sub="What this track rewards, where else rewards the same, and which cars suit it.">
          <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">
            This weekend: <span className="text-ink">{name}</span>
          </span>
        </PageHead>
      }
    >
      <div className="contents">
        <section data-split className="pane flex min-w-[270px] flex-[0_1_300px] flex-col gap-4 rounded-3xl p-[22px]">
          <div>
            <h2 className="display text-[46px] leading-[0.95] font-semibold">{name}</h2>
            <span className="text-[13px] text-ink-3">
              {track.event.race_name}
              {profile.reprofiled ? `, reprofiled ${profile.reprofiled}` : ""}
            </span>
          </div>
          <dl className="grid grid-cols-2 gap-2.5">
            {facts.map(([label, value, mono]) => (
              <div key={label} className="pane-well rounded-2xl px-3 py-2.5">
                <dt className="text-[11px] text-mute">{label}</dt>
                <dd className={`mt-0.5 text-[19px] ${mono ? "figure" : ""}`}>{value}</dd>
              </div>
            ))}
          </dl>
          <div className="pane-well flex flex-1 flex-col justify-between gap-2.5 rounded-[22px] p-4">
            <span className="display text-sm">What it asks of a car</span>
            {DEMANDS.map(([key, label], row) => {
              const level = profile.demand[key] ?? 0;
              return (
                <div key={key} className="grid grid-cols-[76px_minmax(0,1fr)_14px] items-center gap-2.5 text-xs">
                  <span className="text-ink-3">{label}</span>
                  <span className="grid grid-cols-5 gap-[5px]" role="img" aria-label={`${label} ${level} of 5`}>
                    {PIPS.map((color, k) => (
                      <span
                        key={color}
                        className={k < level ? "lit h-[13px] rounded-full" : "pane-well h-[13px] rounded-full"}
                        style={
                          k < level
                            ? {
                                background: color,
                                boxShadow: `0 0 8px ${color}, pane-well 0 1px 0 rgb(255 255 255 / 0.3)`,
                                animationDelay: `${200 + row * 60 + k * 30}ms`,
                              }
                            : undefined
                        }
                      />
                    ))}
                  </span>
                  <span className="figure text-right">{level}</span>
                </div>
              );
            })}
          </div>
        </section>

        <section
          data-split
          className="pane flex min-w-0 flex-[1.1_1_360px] flex-col gap-2.5 rounded-3xl px-5 py-[18px]"
        >
          <div className="flex items-baseline justify-between">
            <h2 className="display text-[18px]">Circuits that ask the same</h2>
            <span className="text-[11px] text-mute">closer is more alike</span>
          </div>
          <div className="pane-well relative min-h-[320px] flex-1 overflow-hidden rounded-3xl">
            <svg
              viewBox="0 0 560 560"
              role="img"
              aria-label={`Circuits most similar to ${name}: ${neighbours.map((item) => `${circuitName(item.circuit_id)} ${plain(item.similarity)}`).join(", ")}`}
              className="absolute inset-0 block h-full w-full"
            >
              <defs>
                <radialGradient id="core">
                  <stop offset="0" stopColor="#ff5f6d" stopOpacity="0.35" />
                  <stop offset="0.5" stopColor="#8a3dff" stopOpacity="0.12" />
                  <stop offset="1" stopColor="#8a3dff" stopOpacity="0" />
                </radialGradient>
              </defs>
              <circle cx="280" cy="280" r="250" fill="url(#core)" />
              {[0.25, 0.5, 0.75].map((ring) => (
                <g key={ring}>
                  <circle cx="280" cy="280" r={ring * 300} fill="none" stroke="#25262b" strokeDasharray="2 6" />
                  <text
                    x={polar(280, 280, ring * 300, -76)[0]}
                    y={polar(280, 280, ring * 300, -76)[1]}
                    fill="#55575f"
                    fontFamily="var(--font-mono)"
                    fontSize="10"
                  >
                    {(1 - ring).toFixed(2)}
                  </text>
                </g>
              ))}
              {nodes.map((node) => (
                <g key={node.circuit_id} className="lit">
                  <line
                    x1="280"
                    y1="280"
                    x2={node.nx}
                    y2={node.ny}
                    stroke={node.color}
                    strokeOpacity="0.35"
                    strokeWidth="1.5"
                  />
                  <circle cx={node.nx} cy={node.ny} r={14 + node.similarity * 16} fill={node.color} opacity="0.16" />
                  <circle
                    cx={node.nx}
                    cy={node.ny}
                    r={5 + node.similarity * 7}
                    fill={node.color}
                    style={{ filter: `drop-shadow(0 0 6px ${node.color})` }}
                  />
                  <text
                    x={node.nx + (node.right ? 18 : -18)}
                    y={node.ny - 2}
                    textAnchor={node.right ? "start" : "end"}
                    fill="#ececef"
                    fontFamily="var(--font-display)"
                    fontSize="17"
                    letterSpacing="1"
                  >
                    {circuitName(node.circuit_id).toUpperCase()}
                  </text>
                  <text
                    x={node.nx + (node.right ? 18 : -18)}
                    y={node.ny + 15}
                    textAnchor={node.right ? "start" : "end"}
                    fill="#a2a3ab"
                    fontFamily="var(--font-mono)"
                    fontSize="12"
                  >
                    {plain(node.similarity)}
                  </text>
                </g>
              ))}
              <circle
                cx="280"
                cy="280"
                r="12"
                fill="#ffffff"
                style={{ filter: "drop-shadow(0 0 10px rgb(255 140 110 / 0.8))" }}
              />
              <text
                x="280"
                y="318"
                textAnchor="middle"
                fill="#ececef"
                fontFamily="var(--font-display)"
                fontSize="15"
                letterSpacing="1.2"
              >
                {name.toUpperCase()}
              </text>
            </svg>
          </div>
        </section>

        <section
          data-split
          className="pane flex min-w-0 flex-[1.2_1_420px] flex-col gap-2.5 rounded-3xl px-5 py-[18px]"
        >
          <div className="flex items-baseline justify-between">
            <h2 className="display text-[18px]">Which cars suit it</h2>
            <span className="text-[11px] text-mute">% off benchmark, left is quicker</span>
          </div>
          <div className="figure grid grid-cols-[96px_minmax(0,1fr)_90px] gap-2.5 text-[10px] text-mute">
            <span />
            <span className="relative block h-3">
              {scale.map((value) => (
                <span key={value} className="absolute -translate-x-1/2" style={{ left: `${x(value)}%` }}>
                  {value > 0 ? `+${value}` : value < 0 ? `−${-value}` : "0"}
                </span>
              ))}
            </span>
            <span className="text-right">two estimates</span>
          </div>
          <div className="flex flex-1 flex-col justify-between gap-1">
            {teams.map((team) => {
              const color = teamColor(team.constructor_id);
              return (
                <div
                  key={team.constructor_id}
                  className="grid grid-cols-[96px_minmax(0,1fr)_90px] items-center gap-2.5"
                >
                  <span className="flex items-center gap-[7px] text-xs">
                    <span
                      className="h-3.5 w-[3px] rounded-sm"
                      style={{ background: color, boxShadow: `0 0 6px ${color}` }}
                    />
                    {teamName(team.constructor_id)}
                  </span>
                  <span
                    className="pane-well relative block h-[26px] rounded-[11px]"
                    title={
                      team.disagree
                        ? "The two estimates disagree here"
                        : `${team.regression?.samples ?? 0} and ${team.similarity?.samples ?? 0} races`
                    }
                  >
                    {scale.map((value) => (
                      <span
                        key={value}
                        className="absolute top-1 bottom-1 w-px bg-white/[0.035]"
                        style={{ left: `${x(value)}%` }}
                      />
                    ))}
                    <Interval
                      estimate={team.regression}
                      x={x}
                      top={5}
                      fill="linear-gradient(90deg,#ffb35c,#ff5f6d)"
                      mark="#ffffff"
                    />
                    <Interval
                      estimate={team.similarity}
                      x={x}
                      top={15}
                      fill="linear-gradient(90deg,#2ee6c5,#3a8bff)"
                      mark="#c9cbd2"
                    />
                  </span>
                  <span className={`figure text-right text-[11px] ${team.disagree ? "text-coral" : "text-ink-3"}`}>
                    {team.regression ? signed(team.regression.value) : "–"} /{" "}
                    {team.similarity ? signed(team.similarity.value) : "–"}
                  </span>
                </div>
              );
            })}
          </div>
          <div className="flex flex-wrap gap-3.5 text-[10.5px] text-mute">
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-4 rounded-full bg-[linear-gradient(90deg,#ffb35c,#ff5f6d)]" />
              from the demand profile
            </span>
            <span className="flex items-center gap-1.5">
              <span className="h-1.5 w-4 rounded-full bg-[linear-gradient(90deg,#2ee6c5,#3a8bff)]" />
              from similar circuits
            </span>
            {teams.some((team) => team.disagree) && <span className="text-coral">red figures: the two disagree</span>}
            {track.dropped_reprofiled > 0 && (
              <span>{track.dropped_reprofiled} results from before the reprofile left out</span>
            )}
          </div>
        </section>
      </div>
    </Frame>
  );
}
