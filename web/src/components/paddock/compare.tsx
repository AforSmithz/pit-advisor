"use client";

import { useState } from "react";
import { Expand } from "@/components/ops/expand";
import { Frame } from "@/components/ops/frame";
import { pct, signed } from "@/lib/format";
import { along } from "@/lib/geometry";
import { circuitName, teamColor, teamName } from "@/lib/teams";
import type { DriverHistory, Estimate, ForecastDriver } from "@/lib/schemas";

const METRICS = [
  ["form", "Form", "quicker", "slower"],
  ["quali_race", "Saturday to Sunday", "Saturday", "Sunday"],
  ["wet", "In the wet", "better wet", "worse wet"],
] as const;

const THIN = 20;

type Side = { history: DriverHistory; entry: ForecastDriver | undefined };

function Picker({
  label,
  value,
  options,
  onChange,
  align,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (code: string) => void;
  align: "left" | "right";
}) {
  return (
    <label
      className={`pane-well flex items-center gap-2 rounded-full py-1 pr-1 pl-3.5 ${align === "right" ? "flex-row-reverse pr-3.5 pl-1" : ""}`}
    >
      <span className="text-[11px] text-mute">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="pane code min-h-9 cursor-pointer rounded-full bg-transparent px-3 text-sm outline-none"
      >
        {options.map((code) => (
          <option key={code} value={code} className="bg-ground">
            {code}
          </option>
        ))}
      </select>
    </label>
  );
}

function Head({ side, align }: { side: Side; align: "left" | "right" }) {
  const color = teamColor(side.history.constructor_id);
  return (
    <div className={`flex items-center gap-3 ${align === "right" ? "flex-row-reverse text-right" : ""}`}>
      <span className="h-16 w-[5px] rounded-sm" style={{ background: color, boxShadow: `0 0 14px ${color}` }} />
      <div>
        <div className="display text-[54px] font-semibold">{side.history.driver_code}</div>
        <div className="mt-1 text-[12.5px] text-ink-3">
          {teamName(side.history.constructor_id)}
          {side.entry?.grid ? `, starts P${side.entry.grid}` : ""}
        </div>
      </div>
    </div>
  );
}

function Band({
  estimate,
  reach,
  color,
  top,
}: {
  estimate: Estimate | null;
  reach: number;
  color: string;
  top: number;
}) {
  if (!estimate) {
    return (
      <span className="absolute text-[10.5px] text-mute" style={{ top, left: "52%" }}>
        no estimate
      </span>
    );
  }
  const x = (value: number) => along(value, -reach, reach) * 100;
  const thin = estimate.samples < THIN;
  return (
    <>
      <span
        className="grow absolute h-2 rounded-full"
        style={{
          top: top + 4,
          left: `${x(estimate.low)}%`,
          width: `${x(estimate.high) - x(estimate.low)}%`,
          background: thin ? "#4e5058" : color,
          opacity: thin ? 1 : 0.8,
          boxShadow: thin ? "none" : `0 0 8px ${color}`,
        }}
      />
      <span
        className="absolute h-4 w-[3px] -translate-x-1/2 rounded-sm bg-white"
        style={{ top, left: `${x(estimate.value)}%` }}
      />
      <span
        className="figure absolute text-[10.5px] whitespace-nowrap"
        style={
          x(estimate.high) > 62
            ? { top: top + 1, right: `calc(${100 - x(Math.min(estimate.low, 0))}% + 10px)` }
            : { top: top + 1, left: `calc(${x(Math.max(estimate.high, 0))}% + 10px)` }
        }
      >
        {signed(estimate.value)} <span className="text-mute">n={estimate.samples}</span>
      </span>
    </>
  );
}

export function Compare({
  head,
  drivers,
  field,
  initial,
}: {
  head: React.ReactNode;
  drivers: DriverHistory[];
  field: ForecastDriver[];
  initial: [string, string];
}) {
  const [left, setLeft] = useState(initial[0]);
  const [right, setRight] = useState(initial[1]);
  const codes = drivers.map((driver) => driver.driver_code);
  const side = (code: string): Side => ({
    history: drivers.find((driver) => driver.driver_code === code)!,
    entry: field.find((driver) => driver.driver_code === code),
  });
  const a = side(left);
  const b = side(right);
  const ca = teamColor(a.history.constructor_id);
  const sameTeam = a.history.constructor_id === b.history.constructor_id;
  const cb = sameTeam ? "#ececef" : teamColor(b.history.constructor_id);

  const sunday = (
    [
      ["win", "Win"],
      ["podium", "Podium"],
      ["points", "Points"],
    ] as const
  ).map(([key, name]) => ({ key, name, a: a.entry?.[key] ?? null, b: b.entry?.[key] ?? null }));

  const since = [...a.history.pace, ...b.history.pace]
    .map((sample) => sample.race_date)
    .sort()
    .slice(-48)[0];
  const paceA = a.history.pace.filter((sample) => sample.regime === "dry" && sample.race_date >= (since ?? ""));
  const paceB = b.history.pace.filter((sample) => sample.regime === "dry" && sample.race_date >= (since ?? ""));
  const all = [...paceA, ...paceB];
  const time = (date: string) => new Date(date).getTime();
  const t0 = Math.min(...all.map((sample) => time(sample.race_date)));
  const t1 = Math.max(...all.map((sample) => time(sample.race_date)));
  const lo = Math.floor(Math.min(...all.map((sample) => sample.percent_off_benchmark)));
  const hi = Math.ceil(Math.max(...all.map((sample) => sample.percent_off_benchmark)));
  const px = (date: string) => 56 + along(time(date), t0, t1) * 568;
  const py = (value: number) => 20 + along(value, lo, hi) * 370;
  const trace = (samples: DriverHistory["pace"]) =>
    samples
      .map(
        (sample, i) =>
          `${i ? "L" : "M"}${px(sample.race_date).toFixed(1)} ${py(sample.percent_off_benchmark).toFixed(1)}`,
      )
      .join(" ");
  const step = hi - lo > 6 ? 2 : 1;
  const ticks = Number.isFinite(lo)
    ? Array.from({ length: Math.floor((hi - lo) / step) + 1 }, (_, i) => lo + i * step)
    : [];
  const pair = [
    { code: a.history.driver_code, samples: paceA, color: ca },
    { code: b.history.driver_code, samples: paceB, color: cb },
  ];
  const month = new Intl.DateTimeFormat("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });

  return (
    <Frame
      head={head}
      strip={
        <section data-split className="pane flex flex-col gap-2 rounded-3xl px-5 py-4">
          <div className="flex items-baseline justify-between">
            <h2 className="display text-[17px]">How they drive</h2>
            <span className="text-[11px] text-mute">centre line is zero, grey is a thin sample</span>
          </div>
          {METRICS.map(([key, name, low, high]) => {
            const ea = a.history[key];
            const eb = b.history[key];
            const reach = Math.max(
              0.5,
              ...[ea, eb].flatMap((estimate) => (estimate ? [Math.abs(estimate.low), Math.abs(estimate.high)] : [])),
            );
            return (
              <div key={key} className="grid grid-cols-[96px_minmax(0,1fr)] items-center gap-3">
                <span className="flex flex-col">
                  <span className="display text-[13px]">{name}</span>
                  <span className="text-[10px] text-mute">
                    {low} · {high}
                  </span>
                </span>
                <div className="pane-well relative h-16 overflow-hidden rounded-2xl">
                  <span className="absolute top-1.5 bottom-1.5 left-1/2 w-px bg-[#34353c]" />
                  <Band estimate={ea} reach={reach} color={ca} top={10} />
                  <Band estimate={eb} reach={reach} color={cb} top={36} />
                </div>
              </div>
            );
          })}
        </section>
      }
    >
      <section data-split className="pane flex flex-wrap items-center justify-between gap-5 rounded-3xl px-6 py-5">
        <div className="flex flex-wrap items-center gap-5">
          <Head side={a} align="left" />
          <Picker
            label="Left"
            value={left}
            options={codes.filter((code) => code !== right)}
            onChange={setLeft}
            align="left"
          />
        </div>
        <div className="pane relative h-[84px] w-[84px] shrink-0 rounded-full">
          <div className="pane-well display absolute inset-3 flex items-center justify-center rounded-full text-xl text-ink-3">
            VS
          </div>
        </div>
        <div className="flex flex-wrap items-center justify-end gap-5">
          <Picker
            label="Right"
            value={right}
            options={codes.filter((code) => code !== left)}
            onChange={setRight}
            align="right"
          />
          <Head side={b} align="right" />
        </div>
      </section>

      <div className="contents">
        <div className="contents">
          <section data-split className="pane flex flex-col gap-2.5 rounded-3xl px-5 py-4">
            <div className="flex items-baseline justify-between">
              <h2 className="display text-[17px]">Sunday, mirrored</h2>
              <span className="text-[11px] text-mute">share of simulated races</span>
            </div>
            {sunday.map((row) => (
              <div
                key={row.key}
                className="grid grid-cols-[56px_minmax(0,1fr)_70px_minmax(0,1fr)_56px] items-center gap-2.5"
              >
                <span className="figure text-right text-[13.5px]">{row.a === null ? "–" : pct(row.a)}</span>
                <span className="pane-well relative flex h-4 justify-end rounded-full p-[3px]">
                  <span
                    className="h-full rounded-full transition-[width] duration-500"
                    style={{ width: `${(row.a ?? 0) * 100}%`, background: ca, boxShadow: `0 0 10px ${ca}` }}
                  />
                </span>
                <span className="text-center text-[11px] tracking-[0.08em] text-ink-3 uppercase">{row.name}</span>
                <span className="pane-well relative flex h-4 rounded-full p-[3px]">
                  <span
                    className="h-full rounded-full transition-[width] duration-500"
                    style={{ width: `${(row.b ?? 0) * 100}%`, background: cb, boxShadow: `0 0 10px ${cb}` }}
                  />
                </span>
                <span className="figure text-[13.5px]">{row.b === null ? "–" : pct(row.b)}</span>
              </div>
            ))}
            <div className="flex justify-between text-[11px] text-mute">
              <span>
                {a.entry ? `8 in 10 finish P${a.entry.position_low} to P${a.entry.position_high}` : "not in this race"}
              </span>
              <span>
                {b.entry ? `8 in 10 finish P${b.entry.position_low} to P${b.entry.position_high}` : "not in this race"}
              </span>
            </div>
          </section>
        </div>
      </div>
      <Expand label="Open the trace">
        <div className="flex items-baseline justify-between">
          <h2 className="display text-[17px]">Race pace, dry races</h2>
          <span className="text-[11px] text-mute">% off benchmark, quicker is up</span>
        </div>
        <div className="pane-well flex-1 rounded-[22px] px-1.5 pt-2 pb-1">
          {all.length === 0 ? (
            <p className="p-4 text-sm text-ink-3">Neither driver has a fitted dry race.</p>
          ) : (
            <svg
              viewBox="0 0 640 420"
              role="img"
              aria-label={`Race pace of ${a.history.driver_code} and ${b.history.driver_code} over their recent dry races`}
              className="block h-full w-full"
            >
              {ticks.map((value) => (
                <g key={value}>
                  <line
                    x1="44"
                    x2="630"
                    y1={py(value)}
                    y2={py(value)}
                    stroke="#1d1e22"
                    strokeDasharray={value === 0 ? undefined : "2 5"}
                  />
                  <text
                    x="36"
                    y={py(value) + 4}
                    textAnchor="end"
                    fill="#7f818a"
                    fontFamily="var(--font-mono)"
                    fontSize="10.5"
                  >
                    {value === 0 ? "0" : value > 0 ? `+${value}` : `−${-value}`}
                  </text>
                </g>
              ))}
              {pair.map(({ code, samples, color }) => (
                <g key={code}>
                  <path
                    d={trace(samples)}
                    fill="none"
                    stroke={color}
                    strokeWidth="2.6"
                    strokeLinejoin="round"
                    style={{ filter: `drop-shadow(0 0 5px ${color})` }}
                  />
                  {samples.map((sample) => (
                    <circle
                      key={`${sample.season}-${sample.round}`}
                      cx={px(sample.race_date)}
                      cy={py(sample.percent_off_benchmark)}
                      r="3.5"
                      fill="#0d0d0f"
                      stroke={color}
                      strokeWidth="2"
                    >
                      <title>{`${circuitName(sample.circuit_id)} ${sample.season}: ${signed(sample.percent_off_benchmark)}%`}</title>
                    </circle>
                  ))}
                </g>
              ))}
              <text x="56" y="412" fill="#7f818a" fontSize="10.5">
                {month.format(new Date(t0))}
              </text>
              <text x="624" y="412" textAnchor="end" fill="#7f818a" fontSize="10.5">
                {month.format(new Date(t1))}
              </text>
            </svg>
          )}
        </div>
        <div className="flex gap-4 text-xs text-ink-3">
          {pair.map(({ code, color }) => (
            <span key={code} className="flex items-center gap-1.5">
              <span className="h-[3px] w-3.5 rounded-sm" style={{ background: color }} />
              {code}
            </span>
          ))}
        </div>
      </Expand>
    </Frame>
  );
}
