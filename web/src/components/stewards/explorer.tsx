"use client";

import { useState } from "react";
import type { Precedent, Ruling } from "@/lib/schemas";

const OUTCOMES = [
  ["none", "No penalty", "#6a6c75"],
  ["warning", "Warning or reprimand", "#58c4ff"],
  ["fine", "Fine", "#2ee6c5"],
  ["time", "Time penalty", "#ffb35c"],
  ["drive_through", "Drive-through or stop-go", "#ff5f6d"],
  ["grid", "Grid drop", "#c03fd0"],
] as const;

const COLOR: Record<string, string> = Object.fromEntries(OUTCOMES.map(([key, , color]) => [key, color]));
const NAME: Record<string, string> = Object.fromEntries(OUTCOMES.map(([key, name]) => [key, name]));

const SESSIONS: Record<string, string> = {
  FP1: "Practice 1",
  FP2: "Practice 2",
  FP3: "Practice 3",
  SQ: "Sprint qualifying",
  S: "Sprint",
  Q: "Qualifying",
  R: "Race",
};

function describe(precedent: Precedent): string {
  const ranked = OUTCOMES.map(([key]) => [key, precedent.outcomes[key] ?? 0] as const)
    .filter(([, count]) => count > 0)
    .sort((a, b) => b[1] - a[1]);
  const parts = ranked.map(([key, count]) => `${count} ${NAME[key]!.toLowerCase()}`);
  const seconds = Object.entries(precedent.seconds)
    .sort((a, b) => b[1] - a[1])
    .map(([length, count]) => `${count} of ${length} seconds`);
  return `${precedent.rulings} rulings: ${parts.join(", ")}.${seconds.length ? ` The time penalties were ${seconds.join(" and ")}.` : ""}`;
}

export function Explorer({
  rulings,
  precedents,
  team,
  since,
}: {
  rulings: Ruling[];
  precedents: Precedent[];
  team: Record<string, string>;
  since: number | null;
}) {
  const [offence, setOffence] = useState(
    rulings.find((ruling) => ruling.offence)?.offence ?? precedents[0]?.offence ?? "",
  );
  const [card, setCard] = useState<number | null>(null);
  const most = Math.max(1, ...precedents.flatMap((precedent) => Object.values(precedent.outcomes)));
  const chosen = precedents.find((precedent) => precedent.offence === offence);

  return (
    <div className="flex flex-1 flex-wrap items-stretch gap-4">
      <section className="raised flex min-w-0 flex-[1.15_1_560px] flex-col gap-3 rounded-[30px] px-5 py-[18px]">
        <div className="flex items-baseline justify-between">
          <h2 className="display text-[19px]">This weekend, {rulings.length} rulings</h2>
          <span className="text-[11.5px] text-mute">tap one to see what usually happens</span>
        </div>
        {rulings.length === 0 && (
          <p className="text-sm text-ink-3">The stewards have not ruled on anything at this event yet.</p>
        )}
        <div className="grid content-start gap-3 [grid-template-columns:repeat(auto-fill,minmax(210px,1fr))]">
          {rulings.map((ruling, index) => {
            const on = card === index || (card === null && ruling.offence === offence && ruling.offence !== null);
            const color = ruling.driver_code ? (team[ruling.driver_code] ?? "#8e909a") : "#8e909a";
            const outcome = ruling.sanction.outcome;
            const lit = outcome !== "none";
            return (
              <button
                key={`${ruling.document}-${ruling.entry}`}
                type="button"
                aria-pressed={on}
                onClick={() => {
                  setCard(index);
                  if (ruling.offence) setOffence(ruling.offence);
                }}
                className={`press flex min-h-[128px] cursor-pointer flex-col gap-1.5 rounded-[20px] px-3.5 py-3 text-left ${
                  on ? "inset shadow-[0_0_0_1px_rgb(192_63_208_/_0.5)]" : "raised-sm"
                }`}
              >
                <span className="flex items-center justify-between gap-2">
                  <span className="flex items-center gap-2">
                    <span className="h-[15px] w-[3px] rounded-sm" style={{ background: color, boxShadow: `0 0 6px ${color}` }} />
                    <span className="code text-[17px]">{ruling.driver_code ?? ruling.competitor ?? "Team"}</span>
                  </span>
                  <span className="text-[10.5px] text-mute">
                    {ruling.session ? (SESSIONS[ruling.session] ?? ruling.session) : ruling.issued?.slice(0, 10)}
                  </span>
                </span>
                <span className="line-clamp-4 text-xs leading-snug text-ink-2">{ruling.fact ?? ruling.outcome}</span>
                <span className="mt-auto flex items-center justify-between gap-2">
                  <span
                    className="rounded-full px-2.5 py-1 text-[10.5px] whitespace-nowrap"
                    style={
                      lit
                        ? { background: COLOR[outcome], color: "#0d0d0f", boxShadow: `0 0 10px ${COLOR[outcome]}` }
                        : { background: "#26272c", color: "#a2a3ab" }
                    }
                  >
                    {ruling.sanction.text.charAt(0).toUpperCase() + ruling.sanction.text.slice(1)}
                  </span>
                  <span className="figure min-w-0 truncate text-[10px] text-faint">{ruling.rule ?? ""}</span>
                </span>
              </button>
            );
          })}
        </div>
      </section>

      <section className="raised flex min-w-0 flex-[1_1_480px] flex-col gap-3 rounded-[30px] px-5 py-[18px]">
        <div className="flex items-baseline justify-between">
          <h2 className="display text-[19px]">What usually happens</h2>
          <span className="text-[11.5px] text-mute">driver rulings{since ? `, ${since} to now` : ""}</span>
        </div>
        <div className="inset overflow-x-auto rounded-[22px] px-3 pt-3.5 pb-2.5">
          <div className="min-w-[460px]">
            <div className="grid grid-cols-[150px_repeat(6,minmax(0,1fr))] items-center gap-1">
              <span />
              {OUTCOMES.map(([key, name]) => (
                <span key={key} className="text-center text-[10px] leading-tight text-[#8e909a]">
                  {name}
                </span>
              ))}
            </div>
            {precedents.map((precedent) => {
              const on = precedent.offence === offence;
              return (
                <button
                  key={precedent.offence}
                  type="button"
                  aria-pressed={on}
                  onClick={() => {
                    setOffence(precedent.offence);
                    setCard(null);
                  }}
                  className={`press mt-1 grid h-[54px] w-full cursor-pointer grid-cols-[150px_repeat(6,minmax(0,1fr))] items-center gap-1 rounded-[14px] text-left ${
                    on ? "raised-sm" : ""
                  }`}
                >
                  <span className="flex flex-col pl-2.5">
                    <span className={`text-[12.5px] ${on ? "text-ink" : "text-ink-3"}`}>{precedent.label}</span>
                    <span className="figure text-[10px] text-mute">{precedent.rulings} rulings</span>
                  </span>
                  {OUTCOMES.map(([key, name, color]) => {
                    const count = precedent.outcomes[key] ?? 0;
                    const size = count ? Math.round(12 + Math.sqrt(count / most) * 30) : 6;
                    return (
                      <span key={key} className="flex h-[46px] items-center justify-center">
                        <span
                          title={`${precedent.label}: ${count} ${name.toLowerCase()}`}
                          className="figure flex items-center justify-center rounded-full text-[10.5px] text-well transition-[opacity,box-shadow] duration-200"
                          style={{
                            width: size,
                            height: size,
                            background: count ? color : "#26272c",
                            boxShadow: count && on ? `0 0 12px ${color}` : "none",
                            opacity: on ? 1 : 0.55,
                          }}
                        >
                          {count >= 4 ? count : ""}
                        </span>
                      </span>
                    );
                  })}
                </button>
              );
            })}
          </div>
        </div>
        {chosen && (
          <div className="glass flex flex-col gap-2 rounded-[22px] px-[18px] py-4">
            <span className="display text-base">{chosen.label}</span>
            <span className="text-[13px] leading-normal text-ink-2">{describe(chosen)}</span>
            <span className="inset-sm flex h-3 gap-0.5 overflow-hidden rounded-full p-0.5">
              {OUTCOMES.filter(([key]) => (chosen.outcomes[key] ?? 0) > 0).map(([key, , color]) => (
                <span
                  key={key}
                  className="h-full rounded-full"
                  style={{ flex: `${chosen.outcomes[key]} 1 0`, background: color }}
                />
              ))}
            </span>
          </div>
        )}
        <p className="text-[11px] leading-snug text-mute">
          Stewards decisions are not binding precedent. This only counts what happened, matched by the
          article cited or, failing that, by the wording of the finding.
        </p>
      </section>
    </div>
  );
}
