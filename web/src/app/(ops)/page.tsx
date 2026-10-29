import { ElevationChip } from "@/components/ops/chips";
import { Frame } from "@/components/ops/frame";
import { Contenders, Favourite, Order, Strip, Verdict } from "@/components/ops/panels";
import { PageHead } from "@/components/tile";
import { loadForecast, loadHistory, loadScenery } from "@/lib/load";

const STAGE: Record<string, string> = {
  before_qualifying: "Before qualifying",
  after_qualifying: "After qualifying",
  after_sprint: "After the sprint",
};

export default async function RaceOps() {
  const [forecast, history] = await Promise.all([loadForecast(), loadHistory()]);
  const scenery = await loadScenery(forecast.event.circuit_id);
  const drivers = [...forecast.drivers].sort((a, b) => b.win - a.win);
  const stage = history?.snapshots.at(-1)?.stage;

  return (
    <Frame
      head={
        <PageHead title={forecast.event.race_name}>
          <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">
            Round {forecast.event.round}
          </span>
          {stage && (
            <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">
              {STAGE[stage] ?? stage}
            </span>
          )}
          {scenery && <ElevationChip />}
        </PageHead>
      }
      strip={<Order drivers={drivers} />}
    >
      <Strip forecast={forecast} />
      {forecast.evidence && <Verdict evidence={forecast.evidence} />}
      <Favourite drivers={drivers} history={history} />
      <Contenders drivers={drivers} />
    </Frame>
  );
}
