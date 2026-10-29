import { Frame } from "@/components/ops/frame";
import { Explorer } from "@/components/stewards/explorer";
import { TurnDetail, TurnStrip } from "@/components/stewards/turns";
import { PageHead } from "@/components/tile";
import { loadForecast, loadStewards } from "@/lib/load";
import { teamColor } from "@/lib/teams";

export const metadata = { title: "Stewards · Pit Advisor" };

export default async function Stewards() {
  const [view, forecast] = await Promise.all([loadStewards(), loadForecast()]);
  if (!view) {
    return (
      <Frame
        head={
          <PageHead
            title="Stewards"
            sub="No stewards view has been published yet. It arrives with the next pipeline run."
          />
        }
      >
        {null}
      </Frame>
    );
  }
  const team = Object.fromEntries(
    forecast.drivers.map((driver) => [driver.driver_code, teamColor(driver.constructor_id)]),
  );
  const penalised = view.rulings.filter((ruling) => ruling.sanction.outcome !== "none").length;
  const stats = [
    [String(view.rulings.length), "rulings this weekend", "text-ink"],
    [String(penalised), "with a penalty", "text-magenta"],
    [
      view.total_rulings.toLocaleString("en"),
      `driver rulings since ${view.from_season ?? "records began"}`,
      "text-ink-3",
    ],
  ] as const;
  return (
    <Frame
      head={
        <PageHead
          title="Stewards"
          sub={`${view.event.race_name} so far, and what the stewards have done before in the same situation.`}
        >
          <dl className="contents">
            {stats.map(([value, label, tone]) => (
              <div
                key={label}
                className="pane inline-flex flex-row-reverse items-baseline gap-1.5 rounded-full px-4 py-2.5"
              >
                <dt className="text-sm text-ink-2">{label}</dt>
                <dd className={`figure text-[13px] ${tone}`}>{value}</dd>
              </div>
            ))}
          </dl>
        </PageHead>
      }
      strip={view.turns.length > 0 ? <TurnStrip turns={view.turns} seasons={view.turn_seasons} /> : undefined}
    >
      <TurnDetail turns={view.turns} />
      <Explorer rulings={view.rulings} precedents={view.precedents} team={team} since={view.from_season} />
    </Frame>
  );
}
