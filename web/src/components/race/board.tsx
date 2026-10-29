"use client";

import { useState } from "react";
import { Expand } from "@/components/ops/expand";
import { Toggle } from "@/components/tile";
import type { ForecastView } from "@/lib/schemas";
import { FinishGrid } from "./finish-grid";
import { WinDial } from "./win-dial";

export function Board({
  race,
  sprint,
  trust,
}: {
  race: ForecastView;
  sprint: ForecastView | null;
  trust: React.ReactNode;
}) {
  const [session, setSession] = useState<"race" | "sprint">("race");
  const shown = session === "sprint" && sprint ? sprint : race;
  return (
    <>
      <section data-split className="pane flex flex-col gap-3 rounded-3xl px-5 py-5">
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
      <Expand key={session} label="All 22 drivers" peek={420}>
        <FinishGrid drivers={shown.drivers} paths={shown.paths} />
      </Expand>
    </>
  );
}
