"use client";

import { useState } from "react";
import { pct, snapshotLabel } from "@/lib/format";
import { teamColor } from "@/lib/teams";
import type { Snapshot } from "@/lib/schemas";

const X0 = 80;
const X1 = 690;
const Y0 = 40;
const Y1 = 256;

const SHORT = new Intl.DateTimeFormat("en-GB", { weekday: "short", day: "numeric", timeZone: "UTC" });

function ceiling(top: number): number {
  for (const step of [0.1, 0.2, 0.3, 0.4, 0.6, 0.8, 1]) if (top <= step) return step;
  return 1;
}

export function Drift({ snapshots }: { snapshots: Snapshot[] }) {
  const shown = snapshots.filter((snapshot) => !snapshot.repeat);
  const [hover, setHover] = useState(shown.length - 1);
  if (shown.length === 0) {
    return <p className="text-sm text-ink-3">No forecast has been published for this race yet.</p>;
  }
  const last = shown[shown.length - 1]!;
  const contenders = new Set<string>();
  for (const snapshot of shown) {
    [...snapshot.drivers]
      .sort((a, b) => b.win - a.win)
      .slice(0, 5)
      .forEach((driver) => contenders.add(driver.driver_code));
  }
  const codes = [...contenders]
    .map((code) => ({ code, end: last.drivers.find((driver) => driver.driver_code === code)?.win ?? 0 }))
    .sort((a, b) => b.end - a.end)
    .slice(0, 6)
    .map((item) => item.code);
  const team = (code: string) =>
    shown.flatMap((snapshot) => snapshot.drivers).find((driver) => driver.driver_code === code)?.constructor_id ?? "";
  const winAt = (snapshot: Snapshot, code: string) =>
    snapshot.drivers.find((driver) => driver.driver_code === code)?.win ?? 0;

  const top = ceiling(Math.max(...shown.flatMap((snapshot) => codes.map((code) => winAt(snapshot, code)))));
  const x = (i: number) => (shown.length === 1 ? (X0 + X1) / 2 : X0 + (i / (shown.length - 1)) * (X1 - X0));
  const y = (value: number) => Y1 - (value / top) * (Y1 - Y0);
  const width = shown.length === 1 ? X1 - X0 : (X1 - X0) / (shown.length - 1);
  const stepped = [0, 0.25, 0.5, 0.75, 1].map((k) => k * top);
  const firstAfter = shown.findIndex((snapshot) => snapshot.stage === "after_qualifying");
  const leaders = codes.slice(0, 2);

  const ends = codes.map((code) => ({ code, y: y(winAt(last, code)) })).sort((a, b) => a.y - b.y);
  const labelAt: Record<string, number> = {};
  let previous = -99;
  for (const end of ends) {
    previous = Math.max(end.y + 4, previous + 13);
    labelAt[end.code] = previous;
  }
  let floor = Y1 + 6;
  for (const end of [...ends].reverse()) {
    floor = Math.min(labelAt[end.code]!, floor);
    labelAt[end.code] = floor;
    floor -= 13;
  }

  const at = shown[Math.min(hover, shown.length - 1)]!;
  const left = (x(hover) / 820) * 100;
  const readout = [...at.drivers].sort((a, b) => b.win - a.win).slice(0, 5);
  const first = shown[0]!;
  const moved = leaders
    .map((code) => `${code} goes from ${pct(winAt(first, code))} to ${pct(winAt(last, code))}`)
    .join("; ");

  return (
    <div className="inset relative flex-1 rounded-[22px] px-1.5 pt-2 pb-0.5">
      <svg
        viewBox="0 0 820 300"
        role="img"
        aria-label={`Chance to win across ${shown.length} published forecasts. ${moved}.`}
        className="block h-full min-h-[240px] w-full"
      >
        {stepped.map((value) => (
          <g key={value}>
            <line x1="44" x2="720" y1={y(value)} y2={y(value)} stroke="#1d1e22" strokeDasharray="2 5" />
            <text x="36" y={y(value) + 4} textAnchor="end" fill="#7f818a" fontFamily="var(--font-mono)" fontSize="10.5">
              {Math.round(value * 100)}%
            </text>
          </g>
        ))}
        {firstAfter > 0 && (
          <>
            <rect
              x={x(firstAfter - 1) + width * 0.28}
              y="14"
              width={width * 0.44}
              height="244"
              rx="12"
              fill="rgb(79 125 255 / 0.08)"
            />
            <text
              x={x(firstAfter - 1) + width * 0.5}
              y="30"
              textAnchor="middle"
              fill="#7ea2ff"
              fontSize="10"
              letterSpacing="1.2"
            >
              QUALIFYING
            </text>
          </>
        )}
        <line x1={x(hover)} x2={x(hover)} y1="14" y2="258" stroke="rgb(236 236 239 / 0.18)" />
        {codes.map((code) => {
          const color = teamColor(team(code));
          const big = leaders.includes(code);
          const points = shown.map((snapshot, i) => [x(i), y(winAt(snapshot, code))] as const);
          return (
            <g key={code}>
              <path
                className="draw"
                d={points.map(([px, py], i) => `${i ? "L" : "M"}${px.toFixed(1)} ${py.toFixed(1)}`).join(" ")}
                fill="none"
                stroke={color}
                strokeWidth={big ? 3 : 1.6}
                strokeOpacity={big ? 1 : 0.6}
                strokeLinejoin="round"
                strokeLinecap="round"
                pathLength={1000}
                style={
                  {
                    "--len": 1000,
                    strokeDasharray: "1000 1000",
                    filter: big ? `drop-shadow(0 0 6px ${color})` : "none",
                  } as React.CSSProperties
                }
              />
              {points.map(([px, py], i) => (
                <circle
                  key={i}
                  cx={px}
                  cy={py}
                  r={i === hover ? 4.5 : 2.6}
                  fill="#0d0d0f"
                  stroke={color}
                  strokeWidth="1.8"
                />
              ))}
              <text x="732" y={labelAt[code]} fill={color} fontFamily="var(--font-display)" fontSize="13" letterSpacing="1">
                {code}
              </text>
              <text x="766" y={labelAt[code]} fill="#a2a3ab" fontFamily="var(--font-mono)" fontSize="10.5">
                {pct(winAt(last, code))}
              </text>
            </g>
          );
        })}
        {shown.map((snapshot, i) => (
          <g key={snapshot.run_id}>
            <text x={x(i)} y="278" textAnchor="middle" fill={i === hover ? "#ececef" : "#7f818a"} fontSize="10.5">
              {snapshotLabel(snapshot)}
            </text>
            <text x={x(i)} y="292" textAnchor="middle" fill="#55575f" fontFamily="var(--font-mono)" fontSize="9">
              {SHORT.format(new Date(snapshot.generated_at))}, {snapshot.generated_at.slice(11, 16)}
            </text>
            <rect
              x={x(i) - width / 2}
              y="14"
              width={width}
              height="244"
              fill="transparent"
              onMouseEnter={() => setHover(i)}
            />
          </g>
        ))}
      </svg>
      <div
        role="status"
        className="glass pointer-events-none absolute top-3 hidden w-[168px] rounded-2xl px-3.5 py-3 sm:block"
        style={{ left: `${left}%`, transform: left > 55 ? "translateX(calc(-100% - 16px))" : "translateX(16px)" }}
      >
        <div className="text-[12.5px] font-medium">{snapshotLabel(at)}</div>
        <div className="mt-0.5 mb-2 text-[10.5px] text-ink-3">
          {SHORT.format(new Date(at.generated_at))}, {at.generated_at.slice(11, 16)} UTC
        </div>
        <div className="grid grid-cols-[auto_auto] gap-x-3 gap-y-0.5 text-[11.5px]">
          {readout.map((driver) => (
            <span key={driver.driver_code} className="contents">
              <span className="code" style={{ color: teamColor(driver.constructor_id) }}>
                {driver.driver_code}
              </span>
              <span className="figure text-right">{pct(driver.win)}</span>
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
