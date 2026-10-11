"use client";

import { useState } from "react";
import { Toggle } from "@/components/tile";
import { pct } from "@/lib/format";
import type { CurvePoint } from "@/lib/schemas";

const NAMES: Record<string, string> = { win: "Win", podium: "Podium", points: "Points" };

export function Reliability({ curves }: { curves: Record<string, CurvePoint[]> }) {
  const events = Object.keys(curves).filter((name) => name in NAMES);
  const [event, setEvent] = useState(events[0] ?? "win");
  const points = (curves[event] ?? []).filter((point) => point.count > 0);
  const most = Math.max(1, ...points.map((point) => point.count));
  const x = (share: number) => 40 + share * 350;
  const y = (share: number) => 230 - share * 220;
  const path = points.map((point, i) => `${i ? "L" : "M"}${x(point.forecast).toFixed(1)} ${y(point.observed).toFixed(1)}`).join(" ");
  return (
    <>
      <div className="flex items-center justify-between gap-2">
        <h2 className="display text-[16px]">Says 30%, happens 30%?</h2>
        <Toggle
          label="Event"
          options={events.map((name) => [name, NAMES[name]!] as const)}
          value={event}
          onChange={setEvent}
          tone="cool"
        />
      </div>
      <div className="inset flex-1 rounded-[18px] p-1.5">
        <svg
          viewBox="0 0 400 252"
          role="img"
          aria-label={`Reliability of the ${event} forecast: forecast share against observed share in ${points.length} bins`}
          className="block h-full min-h-[200px] w-full"
        >
          <defs>
            <linearGradient id="rel" x1="0" y1="1" x2="1" y2="0">
              <stop offset="0" stopColor="#8a3dff" />
              <stop offset="0.5" stopColor="#ff5f6d" />
              <stop offset="1" stopColor="#ffd25a" />
            </linearGradient>
          </defs>
          {[0, 0.5, 1].map((share) => (
            <g key={share}>
              <line x1="40" x2="390" y1={y(share)} y2={y(share)} stroke="#1d1e22" />
              <text x="34" y={y(share) + 4} textAnchor="end" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="9.5">
                {pct(share, 0)}
              </text>
              <text x={x(share)} y="248" textAnchor="middle" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="9.5">
                {pct(share, 0)}
              </text>
            </g>
          ))}
          <line x1={x(0)} y1={y(0)} x2={x(1)} y2={y(1)} stroke="#4e5058" strokeDasharray="4 5" />
          <path
            key={event}
            className="draw"
            d={path}
            fill="none"
            stroke="url(#rel)"
            strokeWidth="2.4"
            strokeLinejoin="round"
            pathLength={1000}
            style={
              {
                "--len": 1000,
                strokeDasharray: "1000 1000",
                filter: "drop-shadow(0 0 6px rgb(255 95 109 / 0.45))",
              } as React.CSSProperties
            }
          />
          {points.map((point) => (
            <circle
              key={point.low}
              cx={x(point.forecast)}
              cy={y(point.observed)}
              r={3 + Math.sqrt(point.count / most) * 9}
              fill="#ff8a4c"
              fillOpacity="0.85"
              stroke="#0d0d0f"
              strokeWidth="2"
            >
              <title>{`Forecast ${pct(point.forecast)}, happened ${pct(point.observed)}, ${point.count} driver-races`}</title>
            </circle>
          ))}
        </svg>
      </div>
      <span className="text-[10.5px] text-mute">
        Across: what the model said. Up: how often it happened. Dot size is the number of driver-races.
      </span>
    </>
  );
}
