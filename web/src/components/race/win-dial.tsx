"use client";

import { useState } from "react";
import { Traced } from "@/components/shell/overlays";
import { Toggle } from "@/components/tile";
import { pct } from "@/lib/format";
import { arc, arcLength, polar } from "@/lib/geometry";
import { teamColor, teamName } from "@/lib/teams";
import type { ForecastView } from "@/lib/schemas";

type Scenario = "all" | "dry" | "mixed" | "wet";

const TITLES: Record<Scenario, string> = {
  all: "CHANCE TO WIN",
  dry: "IF IT STAYS DRY",
  mixed: "IF IT TURNS MIXED",
  wet: "IF IT RAINS",
};

export function WinDial({ forecast, children }: { forecast: ForecastView; children?: React.ReactNode }) {
  const [scenario, setScenario] = useState<Scenario>("all");
  const simulated = forecast.scenarios.filter((item) => item.paths > 0).map((item) => item.scenario);
  const options = [
    ["all", "As forecast"] as const,
    ...(["dry", "mixed", "wet"] as const)
      .filter((name) => simulated.includes(name))
      .map((name) => [name, name.charAt(0).toUpperCase() + name.slice(1)] as const),
  ];
  const winOf = (code: string, fallback: number) =>
    scenario === "all"
      ? fallback
      : (forecast.by_scenario.find((row) => row.scenario === scenario && row.driver_code === code)?.win ?? 0);
  const ranked = forecast.drivers
    .map((driver) => ({ ...driver, shown: winOf(driver.driver_code, driver.win) }))
    .sort((a, b) => b.shown - a.shown);
  const lead = ranked[0]!;

  const ticks = Array.from({ length: 100 }, (_, i) => {
    const big = i % 10 === 0;
    const [x1, y1] = polar(195, 195, 186, i * 3.6);
    const [x2, y2] = polar(195, 195, big ? 177 : 182, i * 3.6);
    return { x1, y1, x2, y2, big };
  });
  let at = 0;
  let needle = 0;
  const segments: { d: string; color: string; first: boolean; length: number; delay: number }[] = [];
  const labels: { x: number; y: number; code: string; first: boolean }[] = [];
  ranked.forEach((driver, index) => {
    const span = driver.shown * 360;
    if (span > 2.6) {
      const from = at + 1.3;
      const to = at + span - 1.3;
      segments.push({
        d: arc(195, 195, 147, from, to),
        color: teamColor(driver.constructor_id),
        first: index === 0,
        length: arcLength(147, from, to),
        delay: 150 + index * 60,
      });
    }
    if (driver.shown > 0.05) {
      const [x, y] = polar(195, 195, 100, at + span / 2);
      labels.push({ x, y: y + 5, code: driver.driver_code, first: index === 0 });
    }
    if (index === 0) needle = at + span / 2;
    at += span;
  });
  const leadColor = teamColor(lead.constructor_id);

  return (
    <div className="flex flex-wrap items-center gap-6">
      <div className="raised relative h-[300px] w-[300px] shrink-0 rounded-full">
        <div className="inset absolute inset-[23px] rounded-full" />
        <div className="raised absolute inset-[51px] rounded-full" />
        <div
          aria-hidden="true"
          className="absolute inset-[92px] rounded-full"
          style={{ background: `radial-gradient(closest-side, ${leadColor}38, transparent)` }}
        />
        <svg
          viewBox="0 0 390 390"
          role="img"
          aria-label={`${TITLES[scenario].toLowerCase()}: ${lead.driver_code} leads at ${pct(lead.shown)}`}
          className="absolute inset-0 h-full w-full"
        >
          {ticks.map((t, i) => (
            <line
              key={i}
              x1={t.x1}
              y1={t.y1}
              x2={t.x2}
              y2={t.y2}
              stroke={t.big ? "#5c5e66" : "#2a2b30"}
              strokeWidth={t.big ? 2 : 1.4}
              strokeLinecap="round"
            />
          ))}
          {segments.map((segment) => (
            <path
              key={`${scenario}-${segment.d}`}
              className="draw"
              d={segment.d}
              fill="none"
              stroke={segment.color}
              strokeOpacity={segment.first ? 1 : 0.75}
              strokeWidth={segment.first ? 16 : 13}
              strokeLinecap="round"
              style={
                {
                  "--len": segment.length,
                  strokeDasharray: `${segment.length} ${segment.length}`,
                  animationDelay: `${segment.delay}ms`,
                  filter: segment.first ? `drop-shadow(0 0 9px ${segment.color})` : "none",
                } as React.CSSProperties
              }
            />
          ))}
          <g
            className="swing"
            style={{ transform: `rotate(${needle}deg)`, transformOrigin: "195px 195px", transformBox: "view-box" }}
          >
            <line x1="195" y1="71" x2="195" y2="83" stroke="#ffffff" strokeWidth="5" strokeLinecap="round" />
          </g>
          {labels.map((label) => (
            <text
              key={label.code}
              x={label.x}
              y={label.y}
              textAnchor="middle"
              fill={label.first ? "#7ea2ff" : "#8e909a"}
              fontFamily="var(--font-display)"
              fontSize="15"
              letterSpacing="1"
            >
              {label.code}
            </text>
          ))}
          <text x="195" y="162" textAnchor="middle" fill="#7f818a" fontSize="12" letterSpacing="1.6">
            {TITLES[scenario]}
          </text>
          <text
            x="195"
            y="216"
            textAnchor="middle"
            fill="#ececef"
            fontFamily="var(--font-display)"
            fontSize="62"
            fontWeight="600"
            letterSpacing="2"
          >
            {lead.driver_code}
          </text>
          <text x="195" y="248" textAnchor="middle" fill="#7ea2ff" fontFamily="var(--font-mono)" fontSize="22">
            {pct(lead.shown)}
          </text>
        </svg>
      </div>

      <div className="flex min-w-[260px] flex-1 flex-col gap-3.5">
        <div>
          <span className="text-xs text-mute">Favourite</span>
          <p className="display text-[26px]">{lead.driver_code}</p>
          <p className="text-[13px] leading-relaxed text-ink-3">
            {teamName(lead.constructor_id)}
            {lead.grid ? `, from P${lead.grid}` : ""}. Wins in{" "}
            <Traced view="forecast_view" label={`${lead.driver_code} to win`} field={`drivers[${lead.driver_code}].win`} className="figure text-sky">
              {pct(lead.win)}
            </Traced>{" "}
            of races, podium in{" "}
            <Traced view="forecast_view" label={`${lead.driver_code} podium`} field={`drivers[${lead.driver_code}].podium`} className="figure">
              {pct(lead.podium)}
            </Traced>
            , eight in ten finish P{lead.position_low} to P{lead.position_high}.
          </p>
        </div>
        {options.length > 1 && (
          <div className="flex flex-col gap-2">
            <span className="text-xs text-mute">If the weather turns</span>
            <Toggle label="Weather scenario" options={options} value={scenario} onChange={setScenario} tone="cool" />
          </div>
        )}
        <div className="flex flex-col gap-1.5">
          <span className="inset-sm flex h-3 gap-0.5 overflow-hidden rounded-full p-[3px]">
            {forecast.scenarios
              .filter((item) => item.weight > 0)
              .map((item) => (
                <span
                  key={item.scenario}
                  className="h-full rounded-full"
                  style={{
                    flex: `${item.weight} 1 0`,
                    background:
                      item.scenario === "dry"
                        ? "linear-gradient(90deg,#2ee6c5,#3a8bff)"
                        : item.scenario === "mixed"
                          ? "#8a5cff"
                          : "#58c4ff",
                  }}
                />
              ))}
          </span>
          <span className="text-[11.5px] text-ink-3">
            {forecast.scenarios.map((item) => `${item.scenario} ${pct(item.weight, 0)}`).join(", ")}
            {forecast.weights_are_forecast ? ", from the weather forecast" : ", from climatology"}
          </span>
        </div>
        {children}
      </div>
    </div>
  );
}
