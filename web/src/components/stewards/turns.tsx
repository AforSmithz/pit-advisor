"use client";

import { useEffect, useState } from "react";
import type { TurnRecord } from "@/lib/schemas";
import { markHeight, TURN_LAYER, TURN_PICK, type TurnMark } from "@/lib/scenery";

const MODES = [
  ["incidents", "Incidents", "magenta"],
  ["track_limits", "Track limits", "signal-hi"],
  ["off_track", "Off track", "sky"],
] as const;

type Mode = (typeof MODES)[number][0];

const SESSIONS: Record<string, string> = {
  race: "Race",
  sprint: "Sprint",
  qualifying: "Qualifying",
  sprint_qualifying: "Sprint qualifying",
  sprint_shootout: "Sprint shootout",
};

function count(turn: TurnRecord, mode: Mode): number {
  return mode === "incidents" ? turn.incidents.length : turn[mode];
}

function usePicked(): [number | null, (turn: number | null) => void] {
  const [picked, setPicked] = useState<number | null>(null);
  useEffect(() => {
    const on = (e: Event) => setPicked((e as CustomEvent<number | null>).detail);
    addEventListener(TURN_PICK, on);
    return () => removeEventListener(TURN_PICK, on);
  }, []);
  return [picked, (turn) => dispatchEvent(new CustomEvent(TURN_PICK, { detail: turn }))];
}

export function TurnStrip({ turns, seasons }: { turns: TurnRecord[]; seasons: number[] }) {
  const modes = MODES.filter(([key]) => turns.some((turn) => count(turn, key) > 0));
  const [mode, setMode] = useState<Mode>(modes[0]?.[0] ?? "incidents");
  const [picked, pick] = usePicked();
  const tone = MODES.find(([key]) => key === mode)![2];
  const shown = turns.filter((turn) => count(turn, mode) > 0);
  const most = Math.max(0, ...shown.map((turn) => count(turn, mode)));

  useEffect(() => {
    const color = MODES.find(([key]) => key === mode)![2];
    const marks: TurnMark[] = turns
      .filter((turn) => count(turn, mode) > 0)
      .map((turn) => ({ number: turn.number, count: count(turn, mode), tone: color }));
    dispatchEvent(new CustomEvent(TURN_LAYER, { detail: marks }));
  }, [turns, mode]);

  useEffect(() => () => void dispatchEvent(new CustomEvent(TURN_LAYER, { detail: [] })), []);

  if (!modes.length) return null;
  const span = seasons.length ? (seasons.length > 1 ? `${seasons[0]} to ${seasons.at(-1)}` : String(seasons[0])) : "";

  return (
    <section data-split className="pane flex flex-col gap-3 rounded-[28px] px-4 py-3.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="display text-[17px]">Where it happens</h2>
        <span className="text-[11px] text-mute">race control here, {span}, by the turn it names</span>
      </div>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label="What to count">
        {modes.map(([key, name, color]) => (
          <button
            key={key}
            type="button"
            aria-pressed={mode === key}
            onClick={() => {
              setMode(key);
              pick(null);
            }}
            className={`press inline-flex items-center gap-2 rounded-full px-3.5 py-1.5 text-[12.5px] ${
              mode === key ? "pane-well text-ink" : "text-ink-3"
            }`}
          >
            <span className="h-2 w-2 rounded-full" style={{ background: `var(--color-${color})` }} />
            {name}
          </button>
        ))}
      </div>
      <div className="flex h-[92px] items-end gap-1 overflow-x-auto pb-0.5">
        {shown.map((turn) => {
          const value = count(turn, mode);
          const on = picked === null || picked === turn.number;
          return (
            <button
              key={turn.number}
              type="button"
              aria-pressed={picked === turn.number}
              aria-label={`Turn ${turn.number}, ${value}`}
              onClick={() => pick(picked === turn.number ? null : turn.number)}
              className="press flex h-full min-w-[30px] flex-1 cursor-pointer flex-col items-center justify-end gap-1"
            >
              <span className={`figure text-[10.5px] ${on ? "text-ink" : "text-faint"}`}>{value}</span>
              <span
                className="w-full max-w-[22px] rounded-t-[5px] transition-opacity duration-200"
                style={{
                  height: markHeight(value, most, 52),
                  background: `var(--color-${tone})`,
                  opacity: on ? 1 : 0.3,
                }}
              />
              <span className={`text-[10.5px] ${picked === turn.number ? "text-ink" : "text-mute"}`}>T{turn.number}</span>
            </button>
          );
        })}
      </div>
    </section>
  );
}

export function TurnDetail({ turns }: { turns: TurnRecord[] }) {
  const [picked, pick] = usePicked();
  const turn = turns.find((item) => item.number === picked);
  if (!turn) return null;
  const tally: [number, string][] = [
    [turn.incidents.length, turn.incidents.length === 1 ? "incident" : "incidents"],
    [turn.track_limits, turn.track_limits === 1 ? "lap deleted for track limits" : "laps deleted for track limits"],
    [turn.off_track, "off or spun and continued"],
  ];
  return (
    <section data-split className="pane flex flex-col gap-3 rounded-3xl px-5 py-[18px]">
      <div className="flex items-baseline justify-between">
        <h2 className="display text-[19px]">Turn {turn.number}</h2>
        <button type="button" onClick={() => pick(null)} className="press text-[11.5px] text-mute hover:text-ink-2">
          Back to the lap
        </button>
      </div>
      <dl className="flex flex-wrap gap-x-5 gap-y-1">
        {tally.map(([value, text]) => (
          <div key={text} className="flex items-baseline gap-1.5">
            <dd className="figure text-[13px] text-ink">{value}</dd>
            <dt className="text-xs text-ink-3">{text}</dt>
          </div>
        ))}
      </dl>
      {turn.incidents.length > 0 && (
        <ul className="flex flex-col gap-1.5">
          {turn.incidents.map((incident, index) => (
            <li key={index} className="pane-well flex flex-col gap-1 rounded-[16px] px-3.5 py-2.5">
              <span className="flex items-center justify-between gap-2">
                <span className="code text-[14px]">{incident.drivers.join(" · ") || "Unnamed"}</span>
                <span className="text-[10.5px] text-mute">
                  {incident.season} {SESSIONS[incident.session] ?? incident.session}
                  {incident.lap ? `, lap ${incident.lap}` : ""}
                </span>
              </span>
              {incident.offence && <span className="text-xs text-ink-2">{incident.offence}</span>}
              <span className={`text-[11px] ${incident.outcome.includes("penalty") ? "text-magenta" : "text-ink-3"}`}>
                {incident.outcome}
              </span>
            </li>
          ))}
        </ul>
      )}
      <p className="text-[11px] leading-snug text-mute">
        Taken from race control messages that name a turn. Decisions made after a session usually arrive as a stewards
        document instead, so an incident here can end at &ldquo;investigated after the session&rdquo;.
      </p>
    </section>
  );
}
