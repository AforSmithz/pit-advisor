"use client";

import { useState } from "react";
import { useFitHeight } from "@/lib/fit";
import { signed } from "@/lib/format";
import { smoothPath } from "@/lib/geometry";
import { circuitName } from "@/lib/teams";
import type { PaceSample } from "@/lib/schemas";

const X0 = 48;
const X1 = 628;
const Y0 = 16;

export function PaceTrace({ samples, code }: { samples: PaceSample[]; code: string }) {
  const [hover, setHover] = useState(samples.length - 1);
  const [box, H] = useFitHeight(640, 250, 200, 520);
  const Y1 = H - 28;
  if (samples.length === 0) {
    return <p className="text-sm text-ink-3">No fitted race for {code} yet.</p>;
  }
  const values = samples.map((sample) => sample.percent_off_benchmark);
  const low = Math.floor(Math.min(...values));
  const high = Math.ceil(Math.max(...values));
  const x = (i: number) => (samples.length === 1 ? (X0 + X1) / 2 : X0 + (i / (samples.length - 1)) * (X1 - X0));
  const y = (value: number) => Y0 + ((value - low) / Math.max(high - low, 1)) * (Y1 - Y0);
  const points = samples.map((sample, i) => [x(i), y(sample.percent_off_benchmark)] as [number, number]);
  const line = smoothPath(points);
  const area = `${line} L${x(samples.length - 1).toFixed(1)} ${Y1} L${x(0).toFixed(1)} ${Y1} Z`;
  const steps = high - low > 6 ? 2 : 1;
  const grid = Array.from({ length: Math.floor((high - low) / steps) + 1 }, (_, i) => low + i * steps);
  const width = (X1 - X0) / Math.max(samples.length - 1, 1);
  const at = samples[hover]!;
  const left = (x(hover) / 640) * 100;
  return (
    <div ref={box} className="pane-well relative min-h-[150px] flex-1 rounded-[20px]">
      <svg
        viewBox={`0 0 640 ${H}`}
        role="img"
        aria-label={`${code} race pace over the last ${samples.length} fitted races, as percent off the session benchmark`}
        className="absolute inset-0 block h-full w-full"
      >
        <defs>
          <linearGradient id="pace-stroke" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0" stopColor="#2ee6c5" />
            <stop offset="0.5" stopColor="#ffb35c" />
            <stop offset="1" stopColor="#ff5f6d" />
          </linearGradient>
          <linearGradient id="pace-fill" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#2ee6c5" stopOpacity="0.32" />
            <stop offset="0.45" stopColor="#ffd25a" stopOpacity="0.26" />
            <stop offset="1" stopColor="#8a3dff" stopOpacity="0.32" />
          </linearGradient>
          <linearGradient id="pace-fade" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" stopColor="#fff" />
            <stop offset="1" stopColor="#fff" stopOpacity="0" />
          </linearGradient>
          <mask id="pace-mask">
            <rect x="0" y="0" width="640" height={H} fill="url(#pace-fade)" />
          </mask>
        </defs>
        {grid.map((value) => (
          <g key={value}>
            <line
              x1="40"
              x2="630"
              y1={y(value)}
              y2={y(value)}
              stroke={value === 0 ? "#2e2f35" : "#1d1e22"}
              strokeDasharray={value === 0 ? undefined : "2 5"}
            />
            <text x="34" y={y(value) + 4} textAnchor="end" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="10">
              {value === 0 ? "0" : value > 0 ? `+${value}` : `\u2212${-value}`}
            </text>
          </g>
        ))}
        <path d={area} fill="url(#pace-fill)" mask="url(#pace-mask)" />
        <path
          className="draw"
          d={line}
          fill="none"
          stroke="url(#pace-stroke)"
          strokeWidth="2.4"
          strokeLinejoin="round"
          pathLength={1000}
          style={
            {
              "--len": 1000,
              strokeDasharray: "1000 1000",
              filter: "drop-shadow(0 0 5px rgb(255 160 90 / 0.45))",
            } as React.CSSProperties
          }
        />
        <line x1={x(hover)} x2={x(hover)} y1="10" y2={Y1 + 4} stroke="rgb(236 236 239 / 0.2)" />
        {samples.map((sample, i) => (
          <g key={`${sample.season}-${sample.round}-${sample.regime}`}>
            <circle
              cx={points[i]![0]}
              cy={points[i]![1]}
              r={i === hover ? 5 : 3.2}
              fill={sample.regime === "dry" ? "#0d0d0f" : "#58c4ff"}
              stroke={i === hover ? "#ffffff" : "#ffb35c"}
              strokeWidth="1.8"
            />
            {(i % 3 === 0 || i === samples.length - 1) && (
              <text x={x(i)} y={H - 6} textAnchor="middle" fill={i === hover ? "#ececef" : "#55575f"} fontSize="8.5">
                {circuitName(sample.circuit_id).slice(0, 6)}
              </text>
            )}
            <rect
              x={x(i) - width / 2}
              y="10"
              width={width}
              height={Y1 - 6}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          </g>
        ))}
      </svg>
      <div
        role="status"
        className="pane pointer-events-none absolute top-3 w-[150px] rounded-2xl px-3.5 py-2.5"
        style={{ left: `${left}%`, transform: left > 55 ? "translateX(calc(-100% - 14px))" : "translateX(14px)" }}
      >
        <div className="text-[12.5px] font-medium">{circuitName(at.circuit_id)}</div>
        <div className="text-[10.5px] text-ink-3">
          {at.race_date}
          {at.regime === "dry" ? "" : `, ${at.regime}`}
        </div>
        <div className="figure mt-1.5 text-[19px] text-signal-hi">{signed(at.percent_off_benchmark)}%</div>
      </div>
    </div>
  );
}
