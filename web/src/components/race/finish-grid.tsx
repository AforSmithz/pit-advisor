"use client";

import { useState } from "react";
import { Toggle } from "@/components/tile";
import { pct } from "@/lib/format";
import { heat } from "@/lib/geometry";
import { teamColor, teamName } from "@/lib/teams";
import type { ForecastDriver } from "@/lib/schemas";

type Order = "grid" | "win" | "expected";

const ORDERS = [
  ["grid", "Grid"],
  ["win", "Win"],
  ["expected", "Expected"],
] as const;

const ROW = 27;

export function FinishGrid({ drivers, paths }: { drivers: ForecastDriver[]; paths: number }) {
  const [order, setOrder] = useState<Order>("grid");
  const [focus, setFocus] = useState<string | null>(null);
  const field = Math.max(...drivers.map((driver) => driver.position.length));
  const brightest = Math.max(...drivers.flatMap((driver) => driver.position));
  const sorted = [...drivers].sort((a, b) =>
    order === "win"
      ? b.win - a.win
      : order === "expected"
        ? a.expected_position - b.expected_position
        : (a.grid ?? 99) - (b.grid ?? 99),
  );
  const at = sorted.findIndex((driver) => driver.driver_code === focus);
  const focused = at >= 0 ? sorted[at] : null;
  const marks = [1, 5, 10, 15, field];

  return (
    <>
      <div className="flex items-center justify-between gap-2.5">
        <h2 className="display text-[19px]">Where they finish</h2>
        <Toggle label="Order by" options={ORDERS} value={order} onChange={setOrder} />
      </div>
      <div className="relative flex-1" onMouseLeave={() => setFocus(null)}>
        <div className="grid grid-cols-[26px_4px_36px_minmax(0,1fr)_50px] items-center gap-x-2 pb-1.5 text-[9.5px] text-mute">
          <span className="text-right">Grid</span>
          <span />
          <span />
          <span className="figure relative h-3">
            {marks.map((mark) => (
              <span
                key={mark}
                className="absolute -translate-x-1/2"
                style={{ left: `${((mark - 0.5) / field) * 100}%` }}
              >
                P{mark}
              </span>
            ))}
          </span>
          <span className="text-right">Win</span>
        </div>
        {sorted.map((driver, index) => {
          const color = teamColor(driver.constructor_id);
          return (
            <button
              key={driver.driver_code}
              type="button"
              onMouseEnter={() => setFocus(driver.driver_code)}
              onFocus={() => setFocus(driver.driver_code)}
              onBlur={() => setFocus(null)}
              aria-label={`${driver.driver_code}, ${teamName(driver.constructor_id)}${driver.grid ? `, starts P${driver.grid}` : ""}: win ${pct(driver.win)}, podium ${pct(driver.podium)}, eight in ten finish P${driver.position_low} to P${driver.position_high}`}
              className={`grid w-full cursor-pointer grid-cols-[26px_4px_36px_minmax(0,1fr)_50px] items-center gap-x-2 rounded-[9px] pr-1 text-left ${
                focus === driver.driver_code ? "bg-white/[0.03]" : ""
              }`}
              style={{ height: ROW }}
            >
              <span className="figure text-right text-[11px] text-mute">{driver.grid ?? "–"}</span>
              <span className="h-3.5 w-[3px] rounded-sm" style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
              <span className="code text-[15px]">{driver.driver_code}</span>
              <span
                className="relative grid h-[15px] gap-[1.5px]"
                style={{ gridTemplateColumns: `repeat(${field}, minmax(0, 1fr))` }}
              >
                {driver.position.map((share, cell) => (
                  <span
                    key={cell}
                    className="lit rounded-[3px]"
                    style={{
                      background: heat(Math.pow(share / brightest, 0.6)),
                      animationDelay: `${500 + index * 24}ms`,
                    }}
                  />
                ))}
                <span
                  className="pointer-events-none absolute -top-[3px] -bottom-[3px] rounded-[5px] border border-white/30"
                  style={{
                    left: `calc(${((driver.position_low - 1) / field) * 100}% - 2px)`,
                    width: `calc(${((driver.position_high - driver.position_low + 1) / field) * 100}% + 2px)`,
                  }}
                />
              </span>
              <span className={`figure text-right text-xs ${driver.win > 0.1 ? "text-ink" : "text-[#8e909a]"}`}>
                {pct(driver.win)}
              </span>
            </button>
          );
        })}
        {focused && (
          <div
            role="status"
            className="glass pointer-events-none absolute left-[70px] w-[220px] rounded-[18px] px-4 py-3.5"
            style={{ top: Math.min(24 + at * ROW + 24, 24 + sorted.length * ROW - 160) }}
          >
            <div className="flex items-baseline gap-2">
              <span className="code text-xl">{focused.driver_code}</span>
              <span className="text-[11.5px] text-ink-3">
                {teamName(focused.constructor_id)}
                {focused.grid ? `, from P${focused.grid}` : ""}
              </span>
            </div>
            <div className="figure mt-2 grid grid-cols-[auto_auto] gap-x-4 gap-y-1 text-[11.5px]">
              <span className="text-ink-3">Win</span>
              <span className="text-right text-sky">{pct(focused.win)}</span>
              <span className="text-ink-3">Podium</span>
              <span className="text-right">{pct(focused.podium)}</span>
              <span className="text-ink-3">Points</span>
              <span className="text-right">{pct(focused.points)}</span>
              <span className="text-ink-3">Expected</span>
              <span className="text-right">P{focused.expected_position.toFixed(1)}</span>
              <span className="text-ink-3">8 in 10</span>
              <span className="text-right">
                P{focused.position_low} to P{focused.position_high}
              </span>
            </div>
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-3 text-[10.5px] text-mute">
        <span className="flex items-center gap-1.5">
          <span
            className="h-[7px] w-11 rounded-[3px]"
            style={{ background: `linear-gradient(90deg, ${[0, 0.22, 0.45, 0.65, 0.83, 1].map(heat).join(",")})` }}
          />
          share of {paths.toLocaleString("en")} races
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-[9px] w-3.5 rounded-[3px] border border-white/30" />8 in 10 finish
        </span>
      </div>
    </>
  );
}
