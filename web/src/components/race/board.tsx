"use client";

import { useState } from "react";
import { Toggle } from "@/components/tile";
import type { ForecastView } from "@/lib/schemas";
import { FinishGrid } from "./finish-grid";
import { WinDial } from "./win-dial";

export function Board({
  race,
  sprint,
  trust,
  drift,
}: {
  race: ForecastView;
  sprint: ForecastView | null;
  trust: React.ReactNode;
  drift: React.ReactNode;
}) {
  const [session, setSession] = useState<"race" | "sprint">("race");
  const shown = session === "sprint" && sprint ? sprint : race;
  return (
    <div className="flex flex-1 flex-wrap items-stretch gap-4">
      <div className="flex min-w-0 flex-[1.6_1_600px] flex-col gap-4">
        <section className="raised flex flex-col gap-3 rounded-[30px] px-6 py-5">
          {sprint && (
            <div className="flex items-center justify-between gap-3">
              <span className="text-xs text-mute">
                {session === "sprint" ? "The sprint, from the sprint grid" : "The grand prix"}
              </span>
              <Toggle
                label="Session"
                options={[
                  ["race", "Race"],
                  ["sprint", "Sprint"],
                ]}
                value={session}
                onChange={setSession}
              />
            </div>
          )}
          <WinDial key={session} forecast={shown}>
            {session === "race" && trust}
          </WinDial>
        </section>
        <section className="raised flex min-h-0 flex-1 flex-col gap-2.5 rounded-[30px] px-5 pt-[18px] pb-3.5">
          <div className="flex items-baseline justify-between gap-3">
            <h2 className="display text-[19px]">How the call moved this week</h2>
            <span className="text-[11.5px] text-mute">chance to win at each published forecast</span>
          </div>
          {drift}
        </section>
      </div>
      <section className="raised flex min-w-0 flex-[1_1_380px] flex-col gap-2.5 rounded-[30px] px-[18px] pt-[18px] pb-3.5">
        <FinishGrid key={session} drivers={shown.drivers} paths={shown.paths} />
      </section>
    </div>
  );
}
