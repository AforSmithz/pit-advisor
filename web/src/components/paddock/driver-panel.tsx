import Link from "next/link";
import { Gauge } from "@/components/gauge";
import { Traced } from "@/components/shell/overlays";
import { PageHead } from "@/components/tile";
import { pct } from "@/lib/format";
import type { DriverView, ForecastView, UpgradesView } from "@/lib/schemas";
import { teamColor, teamName } from "@/lib/teams";
import { PaceTrace } from "./pace-trace";
import { Roster } from "./roster";
import { TeammateBars } from "./teammate-bars";
import { Upgrades } from "./upgrades";

const RECENT = 24;

export function PaddockTabs({ on }: { on: "one" | "compare" }) {
  const tab = (active: boolean) =>
    `press rounded-full px-4 py-2 text-[12.5px] font-medium ${active ? "raised-sm text-ink" : "text-ink-3 hover:text-ink"}`;
  return (
    <nav aria-label="Paddock view" className="inset-sm flex gap-1 rounded-full p-1">
      <Link href="/paddock/" className={tab(on === "one")} aria-current={on === "one" ? "page" : undefined}>
        One driver
      </Link>
      <Link
        href="/paddock/compare/"
        className={tab(on === "compare")}
        aria-current={on === "compare" ? "page" : undefined}
      >
        Head to head
      </Link>
    </nav>
  );
}

export function DriverPanel({
  code,
  view,
  forecast,
  upgrades,
}: {
  code: string;
  view: DriverView;
  forecast: ForecastView;
  upgrades: UpgradesView | null;
}) {
  const driver = view.drivers.find((item) => item.driver_code === code)!;
  const field = forecast.drivers.map((item) => item.driver_code);
  const roster = view.drivers.filter((item) => field.includes(item.driver_code));
  const entry = forecast.drivers.find((item) => item.driver_code === code);
  const color = teamColor(driver.constructor_id);
  const pace = driver.pace.slice(-RECENT);
  const mates = driver.teammate.slice(-RECENT);
  const mate = mates.at(-1)?.teammate;
  const single = mates.every((sample) => sample.teammate === mate);

  return (
    <>
      <PageHead title="Paddock" sub="Who is quick right now, measured against the one driver in the same car.">
        <PaddockTabs on="one" />
      </PageHead>
      <div className="flex flex-1 flex-wrap items-stretch gap-4">
        <section className="raised flex w-full flex-col gap-2 rounded-[30px] px-3 py-4 lg:w-[290px]">
          <div className="flex items-baseline justify-between px-1.5">
            <h2 className="display text-[17px]">Form, the {forecast.event.season} field</h2>
            <span className="text-[10.5px] text-mute">left is quicker</span>
          </div>
          <Roster drivers={roster.length > 0 ? roster : view.drivers} current={code} />
          <span className="px-1.5 text-[10.5px] leading-snug text-mute">
            Percent off the benchmark against the teammate. Grey bands rest on under 20 races.
          </span>
        </section>

        <div className="flex min-w-0 flex-[1_1_600px] flex-col gap-4">
          <section className="raised flex flex-wrap items-center gap-6 rounded-[30px] px-6 py-5">
            <div className="flex w-[220px] flex-col gap-2">
              <div className="flex items-center gap-3">
                <span className="h-12 w-[5px] rounded-sm" style={{ background: color, boxShadow: `0 0 14px ${color}` }} />
                <span className="display text-[56px] font-semibold">{code}</span>
              </div>
              <span className="text-[13.5px] text-ink-3">{teamName(driver.constructor_id)}</span>
              <dl className="inset-sm grid grid-cols-[1fr_auto] gap-x-3 gap-y-1.5 rounded-2xl px-3.5 py-3 text-[12.5px]">
                {entry ? (
                  <>
                    <dt className="text-mute">Starts</dt>
                    <dd className="figure text-right">{entry.grid ? `P${entry.grid}` : "–"}</dd>
                    <dt className="text-mute">Chance to win</dt>
                    <dd className="text-right">
                      <Traced
                        view="forecast_view"
                        label={`${code} to win`}
                        field={`drivers[${code}].win`}
                        className="figure text-signal"
                      >
                        {pct(entry.win)}
                      </Traced>
                    </dd>
                  </>
                ) : (
                  <>
                    <dt className="text-mute">Last raced</dt>
                    <dd className="figure text-right">{driver.last_race_date}</dd>
                  </>
                )}
                {mate && (
                  <>
                    <dt className="text-mute">Teammate</dt>
                    <dd className="text-right">
                      <Link href={`/paddock/${mate}/`} className="code text-[13px] hover:text-signal">
                        {mate}
                      </Link>
                    </dd>
                  </>
                )}
              </dl>
            </div>
            <div className="flex flex-[1_1_560px] flex-wrap justify-around gap-4">
              <Gauge id="g-form" title="Form" estimate={driver.form} span={1} colors={["#2ee6c5", "#3a8bff"]} />
              <Gauge
                id="g-quali"
                title="Saturday to Sunday"
                estimate={driver.quali_race}
                span={1.5}
                colors={["#ffb35c", "#ff5f6d"]}
                left="Saturday"
                right="Sunday"
              />
              <Gauge
                id="g-wet"
                title="In the wet"
                estimate={driver.wet}
                span={2.5}
                colors={["#58c4ff", "#8a5cff"]}
                left="better wet"
                right="worse wet"
              />
            </div>
          </section>

          <div className="flex flex-1 flex-wrap items-stretch gap-4">
            <section className="raised flex min-w-0 flex-[1.4_1_440px] flex-col gap-2.5 rounded-[28px] px-5 py-4">
              <div className="flex items-baseline justify-between">
                <h2 className="display text-[17px]">Race pace, last {pace.length} races</h2>
                <span className="text-[11px] text-mute">% off benchmark, quicker is up, blue is wet</span>
              </div>
              <PaceTrace samples={pace} code={code} />
            </section>
            <div className="flex min-w-0 flex-[1_1_300px] flex-col gap-4">
              <section className="raised flex flex-1 flex-col gap-1.5 rounded-[26px] px-4 pt-3.5 pb-2.5">
                <div className="flex items-baseline justify-between">
                  <h2 className="display text-[16px]">Against {single && mate ? mate : "teammates"}</h2>
                  <span className="text-[10.5px] text-mute">up, {code} quicker</span>
                </div>
                <TeammateBars samples={mates} code={code} />
              </section>
              {upgrades && (
                <section className="raised flex flex-col gap-2 rounded-[26px] px-4 pt-3.5 pb-3">
                  <div className="flex items-baseline justify-between">
                    <h2 className="display text-[16px]">
                      Upgrades, {teamName(driver.constructor_id)} {upgrades.event.season}
                    </h2>
                    <span className="text-[10.5px] text-mute">parts per round</span>
                  </div>
                  <Upgrades view={upgrades} team={driver.constructor_id} />
                </section>
              )}
            </div>
          </div>
        </div>
      </div>
    </>
  );
}
