import { Explorer } from "@/components/stewards/explorer";
import { PageHead } from "@/components/tile";
import { loadForecast, loadStewards } from "@/lib/load";
import { teamColor } from "@/lib/teams";

export const metadata = { title: "Stewards · Pit Advisor" };

export default async function Stewards() {
  const [view, forecast] = await Promise.all([loadStewards(), loadForecast()]);
  if (!view) {
    return (
      <PageHead title="Stewards" sub="No stewards view has been published yet. It arrives with the next pipeline run." />
    );
  }
  const team = Object.fromEntries(
    forecast.drivers.map((driver) => [driver.driver_code, teamColor(driver.constructor_id)]),
  );
  const penalised = view.rulings.filter((ruling) => ruling.sanction.outcome !== "none").length;
  const stats = [
    [String(view.rulings.length), "rulings this weekend", "text-ink"],
    [String(penalised), "with a penalty", "text-magenta"],
    [view.total_rulings.toLocaleString("en"), `driver rulings since ${view.from_season ?? "records began"}`, "text-ink-3"],
  ] as const;
  return (
    <>
      <PageHead
        title="Stewards"
        sub={`${view.event.race_name} so far, and what the stewards have done before in the same situation.`}
      >
        <dl className="flex gap-2.5">
          {stats.map(([value, label, tone]) => (
            <div key={label} className="inset-sm flex flex-col-reverse items-end rounded-2xl px-3.5 py-2">
              <dt className="text-[10.5px] text-mute">{label}</dt>
              <dd className={`figure text-lg ${tone}`}>{value}</dd>
            </div>
          ))}
        </dl>
      </PageHead>
      <Explorer rulings={view.rulings} precedents={view.precedents} team={team} since={view.from_season} />
    </>
  );
}
