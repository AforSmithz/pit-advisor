import { BASELINES, pct, stamp } from "@/lib/format";
import type { ForecastDriver, ForecastView, HistoryView } from "@/lib/schemas";
import { teamColor, teamName } from "@/lib/teams";

function Split({ text }: { text: string }) {
  const [whole, part] = text.split(".");
  return (
    <>
      {whole}
      {part !== undefined && <span className="text-faint">.{part}</span>}
    </>
  );
}

function Arrow() {
  return (
    <svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true" className="text-mute">
      <path d="M5 11l6-6M6 5h5v5" stroke="currentColor" strokeWidth="1.5" fill="none" strokeLinecap="round" />
    </svg>
  );
}

export function Strip({ forecast }: { forecast: ForecastView }) {
  const dry = forecast.scenarios.find((s) => s.scenario === "dry");
  const segments: [string, string][] = [
    [String(forecast.drivers.length), "drivers"],
    [forecast.paths.toLocaleString("en"), "races simulated"],
    [dry ? pct(dry.weight, 0) : "n/a", "dry"],
    [String(forecast.laps), "laps"],
  ];
  return (
    <div data-split className="pane grid grid-cols-2 gap-1.5 rounded-[28px] p-1.5 sm:grid-cols-4">
      {segments.map(([figure, text]) => (
        <div key={text} className="pane-well ops-content rounded-full px-2 py-3 text-center text-sm text-ink-2">
          <b className="figure mr-1.5 text-[13px] font-normal text-ink">{figure}</b>
          {text}
        </div>
      ))}
    </div>
  );
}

export function Verdict({ evidence }: { evidence: NonNullable<ForecastView["evidence"]> }) {
  const baselines = Object.keys(BASELINES).filter((name) => name in evidence.log_loss);
  const beaten = baselines.filter((name) => evidence.separated_from.includes(name));
  const level = baselines.filter((name) => !evidence.separated_from.includes(name));
  const names = (list: string[]) => list.map((name) => BASELINES[name]).join(", ");
  const tiles = [
    { n: beaten.length, title: "Beats", sub: beaten.length ? `${names(beaten)} over ${evidence.races} races` : "no simple guess yet", tone: "#4fd18b", mark: "M4 8.5l2.5 2.5L12 5.5" },
    { n: level.length, title: "Within noise", sub: level.length ? `${names(level)} baseline` : "none", tone: "#ffb35c", mark: "M8 4v5M8 11.5v.5" },
  ];
  return (
    <div className="grid grid-cols-2 gap-3">
      {tiles.map((tile) => (
        <a key={tile.title} href="/record/" data-split className="pane grid min-h-28 grid-cols-[1fr_auto] items-end rounded-3xl px-5 py-4">
          <div className="ops-content">
            <svg width="22" height="22" viewBox="0 0 16 16" aria-hidden="true">
              <circle cx="8" cy="8" r="7" fill={tile.tone} />
              <path d={tile.mark} stroke="#111113" strokeWidth="1.8" fill="none" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <div className="mt-2.5 text-[15px] text-ink-2">{tile.title}</div>
            <div className="mt-0.5 text-xs text-mute">{tile.sub}</div>
          </div>
          <div className="ops-content text-[52px] leading-[0.9] font-extralight">{tile.n}</div>
        </a>
      ))}
    </div>
  );
}

const TOP = 5;

// keeps the last label and drops any that would crowd a neighbour
function spaced(xs: number[], gap: number): number[] {
  const last = xs.length - 1;
  const kept: number[] = [];
  for (let i = 0; i < xs.length; i++) {
    const prev = kept.length ? xs[kept[kept.length - 1]!]! : -Infinity;
    if (i === last || (xs[i]! - prev >= gap && xs[last]! - xs[i]! >= gap)) kept.push(i);
  }
  return kept;
}

function Drift({ drivers, history }: { drivers: ForecastDriver[]; history: HistoryView }) {
  const snaps = history.snapshots;
  const top = drivers.slice(0, TOP);
  const win = (snap: HistoryView["snapshots"][number], code: string) => snap.drivers.find((d) => d.driver_code === code)?.win ?? 0;
  const max = Math.max(...snaps.flatMap((s) => top.map((d) => win(s, d.driver_code))), 0.01) * 1.12;
  const W = 480, H = 128, pad = 10;
  const x = (i: number) => pad + (i * (W - 2 * pad)) / Math.max(1, snaps.length - 1);
  const y = (v: number) => H - (v / max) * (H - 8);
  const seen = new Set<string>();
  const ticks = spaced(snaps.map((_, i) => x(i)), 130).map((i) => ({ s: snaps[i]!, i }));
  const end = snaps.length - 1;
  return (
    <div className="pane-well mt-3.5 rounded-2xl px-3 pt-3 pb-1.5">
      <div className="ops-content">
        <svg viewBox={`0 0 ${W} ${H + 22}`} preserveAspectRatio="none" className="block h-[150px] w-full overflow-visible" role="img" aria-label="Win chance of the top five across published forecasts">
          {[0.05, 0.1, 0.15, 0.2, 0.25].filter((g) => g < max).map((g) => (
            <g key={g}>
              <line x1="0" x2={W} y1={y(g)} y2={y(g)} stroke="rgb(255 255 255 / 0.06)" strokeDasharray="3 5" />
              <text x={W} y={y(g) - 3} textAnchor="end" className="figure fill-faint text-[10px]">{pct(g, 0)}</text>
            </g>
          ))}
          {top.map((d) => {
            const dashed = seen.has(d.constructor_id);
            seen.add(d.constructor_id);
            const points = snaps.map((s, i) => `${x(i)},${y(win(s, d.driver_code))}`).join(" ");
            return (
              <g key={d.driver_code}>
                <polyline fill="none" stroke={teamColor(d.constructor_id)} strokeWidth="1.6" strokeDasharray={dashed ? "5 4" : undefined} points={points} />
                <circle cx={x(end)} cy={y(win(snaps[end]!, d.driver_code))} r="3" fill={teamColor(d.constructor_id)} />
              </g>
            );
          })}
          {ticks.map(({ s, i }) => (
            <text key={s.run_id} x={x(i)} y={H + 18} textAnchor={i === 0 ? "start" : i === snaps.length - 1 ? "end" : "middle"} className="figure fill-faint text-[10px]">
              {stamp(s.generated_at)}
            </text>
          ))}
        </svg>
        <div className="mt-2.5 flex flex-wrap gap-x-3.5 gap-y-1 text-xs text-mute">
          {top.map((d) => (
            <span key={d.driver_code} className="figure inline-flex items-center gap-1.5">
              <i className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: teamColor(d.constructor_id) }} />
              {d.driver_code} {pct(d.win)}
            </span>
          ))}
          <span className="ml-auto">published forecasts</span>
        </div>
      </div>
    </div>
  );
}

export function Favourite({ drivers, history }: { drivers: ForecastDriver[]; history: HistoryView | null }) {
  const fav = drivers[0];
  if (!fav) return null;
  return (
    <a href="/forecast/" data-split className="pane block rounded-3xl px-5.5 pt-5 pb-4.5">
      <div className="ops-content">
        <div className="flex items-baseline justify-between">
          <h3 className="text-[15px] font-normal text-ink-2">Win probability, favourite</h3>
          <Arrow />
        </div>
        <div className="mt-3.5 text-[64px] leading-none font-extralight tracking-[-0.03em]">
          <Split text={pct(fav.win).replace("%", "")} />
          <span className="ml-1 text-lg tracking-normal text-mute">%</span>
        </div>
        <div className="mt-1.5 flex items-center gap-2.5 text-sm text-ink-2">
          <i className="inline-block h-2.5 w-2.5 rounded-[3px]" style={{ background: teamColor(fav.constructor_id) }} />
          {fav.driver_code}, {teamName(fav.constructor_id)}
          <span className="ml-auto text-xs text-mute">podium {pct(fav.podium)}</span>
        </div>
      </div>
      {history && history.snapshots.length > 1 && <Drift drivers={drivers} history={history} />}
    </a>
  );
}

export function Contenders({ drivers }: { drivers: ForecastDriver[] }) {
  return (
    <div className="grid grid-cols-2 gap-3">
      {drivers.slice(0, 4).map((d) => {
        const colour = teamColor(d.constructor_id);
        const peak = Math.max(...d.position);
        return (
          <a key={d.driver_code} href={`/paddock/${d.driver_code}/`} data-split className="pane block rounded-3xl px-4 pt-4 pb-3.5">
            <div className="ops-content flex items-start justify-between">
              <div>
                <div className="display text-[28px] leading-none">{d.driver_code}</div>
                <div className="mt-1 text-xs text-mute">{teamName(d.constructor_id)}</div>
              </div>
              <Arrow />
            </div>
            <div className="pane-well mt-3 flex h-[74px] items-end gap-0.5 rounded-xl px-2 pt-2.5 pb-1.5">
              {d.position.map((p, i) => {
                const place = i + 1;
                const inside = place >= d.position_low && place <= d.position_high;
                return (
                  <div
                    key={place}
                    className="ops-content min-h-px flex-1 rounded-t-[2px]"
                    style={{ height: `${(p / peak) * 100}%`, background: colour, opacity: inside ? (place <= 3 ? 1 : 0.55) : 0.18 }}
                  />
                );
              })}
            </div>
            <div className="ops-content">
              <div className="figure mx-0.5 mt-1 flex justify-between text-[10px] text-faint">
                <span>P1</span>
                <span>P{d.position.length}</span>
              </div>
              <div className="mt-2.5 flex flex-wrap justify-between gap-x-2 text-xs text-mute">
                <span>Win <b className="figure text-[13px] font-normal text-ink">{pct(d.win)}</b></span>
                <span>Podium <b className="figure text-[13px] font-normal text-ink">{pct(d.podium)}</b></span>
                <span>P{d.position_low} to P{d.position_high}</span>
              </div>
            </div>
          </a>
        );
      })}
    </div>
  );
}

export function Order({ drivers }: { drivers: ForecastDriver[] }) {
  const sorted = [...drivers].sort((a, b) => a.expected_position - b.expected_position);
  const peak = Math.max(...drivers.map((d) => d.podium), 0.01);
  return (
    <div data-split className="pane hidden rounded-3xl px-5 pt-4 pb-3.5 lg:block">
      <div className="ops-content flex items-baseline justify-between">
        <h3 className="text-[15px] font-normal text-ink-2">Expected finishing order</h3>
        <span className="text-xs text-mute">bar height is podium chance</span>
      </div>
      <div className="pane-well mt-3 grid items-end gap-1 rounded-2xl px-2.5 pt-2.5 pb-2" style={{ gridTemplateColumns: `repeat(${sorted.length}, minmax(0, 1fr))` }}>
        {sorted.map((d) => (
          <div key={d.driver_code} className="ops-content flex flex-col items-center gap-1.5">
            <div className="w-full max-w-[18px] rounded-t-[3px] rounded-b-[1px]" style={{ height: 8 + (d.podium / peak) * 64, background: teamColor(d.constructor_id) }} />
            <div className="figure text-[10px] text-ink-2">{d.driver_code}</div>
            <div className="figure text-[9px] text-faint">{d.expected_position.toFixed(1)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
