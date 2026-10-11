"use client";

import { useState } from "react";
import { plain } from "@/lib/format";
import { along } from "@/lib/geometry";
import { circuitName } from "@/lib/teams";
import type { CalibrationView } from "@/lib/schemas";

const X0 = 52;
const X1 = 744;
const Y0 = 34;
const Y1 = 296;

export function RaceByRace({
  races,
  model,
  baseline,
}: {
  races: CalibrationView["per_race"];
  model: string;
  baseline: string;
}) {
  const [hover, setHover] = useState(races.length - 1);
  const scored = races.filter((race) => model in race.log_loss && baseline in race.log_loss);
  if (scored.length === 0) return <p className="text-sm text-ink-3">No race has been scored yet.</p>;
  const losses = scored.flatMap((race) => [race.log_loss[model]!, race.log_loss[baseline]!]);
  const lo = Math.floor(Math.min(...losses) * 10) / 10;
  const hi = Math.ceil(Math.max(...losses) * 10) / 10;
  const slot = (X1 - X0) / scored.length;
  const x = (i: number) => X0 + slot * (i + 0.5);
  // lower log loss is the better forecast, so it sits higher on the chart
  const y = (loss: number) => Y0 + along(loss, lo, hi) * (Y1 - Y0);
  const ticks = Array.from({ length: Math.round((hi - lo) / 0.1) + 1 }, (_, i) => lo + i * 0.1).filter(
    (_, i, all) => all.length <= 6 || i % 2 === 0,
  );
  const seasons = scored
    .map((race, i) => ({ season: race.season, i }))
    .filter((item, i, all) => i === 0 || all[i - 1]!.season !== item.season);
  const at = scored[Math.min(hover, scored.length - 1)]!;
  const left = (x(Math.min(hover, scored.length - 1)) / 760) * 100;

  return (
    <div className="inset relative flex-1 rounded-[22px] px-1.5 pt-2 pb-0.5">
      <svg
        viewBox="0 0 760 330"
        role="img"
        aria-label={`Log loss per race for the model and ${baseline.replace("_", " ")} over ${scored.length} races, lower is better`}
        className="block h-full min-h-[260px] w-full"
      >
        {ticks.map((value) => (
          <g key={value}>
            <line x1="44" x2="750" y1={y(value)} y2={y(value)} stroke="#1d1e22" strokeDasharray="2 5" />
            <text x="38" y={y(value) + 4} textAnchor="end" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="10">
              {plain(value, 1)}
            </text>
          </g>
        ))}
        {seasons.map(({ season, i }) => (
          <g key={season}>
            <line x1={x(i) - slot / 2} x2={x(i) - slot / 2} y1="10" y2={Y1 + 4} stroke="#2e2f35" strokeDasharray="3 4" />
            <text x={x(i) - slot / 2 + 6} y="22" fill="#7f818a" fontSize="10.5">
              {season}
            </text>
          </g>
        ))}
        <line x1={x(hover)} x2={x(hover)} y1="10" y2={Y1 + 4} stroke="rgb(236 236 239 / 0.2)" />
        {scored.map((race, i) => {
          const ym = y(race.log_loss[model]!);
          const yg = y(race.log_loss[baseline]!);
          const better = ym < yg;
          return (
            <g key={`${race.season}-${race.round}`} className="lit" style={{ animationDelay: `${200 + i * 12}ms` }}>
              <line
                x1={x(i)}
                x2={x(i)}
                y1={ym}
                y2={yg}
                stroke={better ? "rgb(255 138 76 / 0.45)" : "rgb(58 139 255 / 0.45)"}
                strokeWidth="1.6"
                strokeLinecap="round"
              />
              <circle cx={x(i)} cy={yg} r={i === hover ? 5 : 3.4} fill="#3a8bff" />
              <circle
                cx={x(i)}
                cy={ym}
                r={i === hover ? 5.5 : 4}
                fill="#ff8a4c"
                style={{ filter: "drop-shadow(0 0 4px rgb(255 138 76 / 0.7))" }}
              />
              <rect x={x(i) - slot / 2} y="10" width={slot} height={Y1 - 6} fill="transparent" onMouseEnter={() => setHover(i)} />
            </g>
          );
        })}
      </svg>
      <div
        role="status"
        className="glass pointer-events-none absolute top-3 w-[170px] rounded-2xl px-3.5 py-2.5"
        style={{ left: `${left}%`, transform: left > 55 ? "translateX(calc(-100% - 14px))" : "translateX(14px)" }}
      >
        <div className="text-[12.5px] font-medium">
          {circuitName(at.circuit_id)} {at.season}
        </div>
        <div className="text-[10.5px] text-ink-3">
          {at.race_date}, {at.starters} starters
        </div>
        <div className="figure mt-1.5 grid grid-cols-[auto_auto] gap-x-3 text-xs">
          <span className="text-signal">Model</span>
          <span className="text-right">{plain(at.log_loss[model]!, 3)}</span>
          <span className="text-blue">{baseline === "grid" ? "Grid" : baseline.replace("_", " ")}</span>
          <span className="text-right">{plain(at.log_loss[baseline]!, 3)}</span>
        </div>
      </div>
    </div>
  );
}
