import { Gauge } from "@/components/gauge";
import { Expand } from "@/components/ops/expand";
import { Frame } from "@/components/ops/frame";
import { Debrief } from "@/components/record/debrief";
import { RaceByRace } from "@/components/record/race-by-race";
import { Reliability } from "@/components/record/reliability";
import { PageHead } from "@/components/tile";
import { BASELINES, gain, plain } from "@/lib/format";
import { along } from "@/lib/geometry";
import { loadCalibration, loadDebrief } from "@/lib/load";
import type { Bounds } from "@/lib/schemas";
import { circuitName } from "@/lib/teams";

export const metadata = { title: "Track record · Pit Advisor" };

const KNOBS: [string, string][] = [
  ["#ffb35c", "#ff5f6d"],
  ["#2ee6c5", "#3a8bff"],
  ["#c03fd0", "#8a5cff"],
];

const EVENTS: Record<string, string> = { win: "Win", podium: "Podium", points: "Points" };

function asEstimate(bounds: Bounds) {
  return {
    value: bounds.value,
    low: bounds.low ?? bounds.value,
    high: bounds.high ?? bounds.value,
    samples: bounds.races,
  };
}

export default async function Record() {
  const [view, debrief] = await Promise.all([loadCalibration(), loadDebrief()]);
  const model = view.scored.find((item) => item.name === view.model_name);
  const raw = view.scored.find((item) => item.name === "raw_simulation");
  const first = view.per_race[0];
  const last = view.per_race.at(-1);
  const paired = view.paired.filter((item) => item.baseline in BASELINES);
  const reach =
    Math.ceil(
      Math.max(
        0.05,
        ...paired.flatMap((item) => [Math.abs(item.log_loss_gain.low ?? 0), Math.abs(item.log_loss_gain.high ?? 0)]),
      ) * 20,
    ) / 20;
  const marketReach = Math.max(
    0.01,
    ...view.market.flatMap((item) => [Math.abs(item.log_loss_gain.low ?? 0), Math.abs(item.log_loss_gain.high ?? 0)]),
  );
  const mx = (value: number) => along(value, -marketReach, marketReach) * 100;
  const sources = [...new Set(view.market.flatMap((item) => Object.keys(item.sources)))];

  return (
    <Frame
      head={
        <PageHead
          title="Track record"
          sub="Every call scored against what happened, and against three simple guesses anyone could make."
        >
          {first && last && (
            <span className="pane inline-flex items-center rounded-full px-4 py-2.5 text-sm text-ink-2">
              {view.per_race.length} races, {first.season} {circuitName(first.circuit_id)} to {last.season}{" "}
              {circuitName(last.circuit_id)}
            </span>
          )}
        </PageHead>
      }
      strip={
        <Expand label="Every race" height={250}>
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <h2 className="display text-[18px]">Race by race</h2>
            <span className="flex gap-3.5 text-[11px] text-ink-3">
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-signal" />
                model
              </span>
              <span className="flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full bg-blue" />
                grid order
              </span>
              <span className="text-mute">log loss, lower is up and better</span>
            </span>
          </div>
          <RaceByRace races={view.per_race} model={view.model_name} baseline="grid" />
        </Expand>
      }
    >
      <div className="contents">
        <section data-split className="pane flex min-w-0 flex-[1.6_1_620px] flex-col gap-3 rounded-3xl px-5 py-4">
          <div className="flex items-baseline justify-between">
            <h2 className="display text-[18px]">Better than guessing?</h2>
            <span className="text-[11px] text-mute">log loss gain, right of zero is better</span>
          </div>
          <div className="flex flex-wrap justify-around gap-4">
            {paired.map((item, i) => {
              const separated = view.separated_from.includes(item.baseline);
              const worse = (item.log_loss_gain.high ?? 0) < 0;
              return (
                <Gauge
                  key={item.baseline}
                  id={`gain-${item.baseline}`}
                  title={`Against ${BASELINES[item.baseline]}`}
                  estimate={asEstimate(item.log_loss_gain)}
                  span={reach}
                  colors={KNOBS[i % KNOBS.length]!}
                  size={146}
                  left="guess better"
                  right="model better"
                  threshold={0}
                  digits={3}
                  caption={
                    separated
                      ? `clearly better, ${item.log_loss_gain.races} races`
                      : worse
                        ? `clearly worse, ${item.log_loss_gain.races} races`
                        : `level within the noise, ${item.log_loss_gain.races} races`
                  }
                />
              );
            })}
          </div>
          {model && raw && (
            <p className="text-[11.5px] leading-snug text-mute">
              The published forecast pools the simulation with the grid. Over the same {model.races} races it scores a
              log loss of <span className="figure text-ink-2">{plain(model.log_loss.value, 3)}</span>, the raw
              simulation before pooling <span className="figure text-ink-2">{plain(raw.log_loss.value, 3)}</span>.
            </p>
          )}
        </section>
        {debrief && <Debrief view={debrief} />}
      </div>

      <div className="contents">
        <div className="contents">
          {model && (
            <section data-split className="pane flex flex-[1.4] flex-col gap-2 rounded-3xl px-4 pt-3.5 pb-2.5">
              <Reliability curves={model.curves} />
            </section>
          )}
          {view.market.length > 0 && (
            <section data-split className="pane flex flex-1 flex-col gap-2 rounded-3xl px-4 py-3.5">
              <div className="flex items-baseline justify-between">
                <h2 className="display text-[16px]">Against the betting market</h2>
                <span className="text-[10.5px] text-mute">
                  {sources.join(", ")}, {Math.max(...view.market.map((item) => item.races))} races
                </span>
              </div>
              {view.market.map((item) => {
                const bounds = item.log_loss_gain;
                return (
                  <div
                    key={item.event}
                    className="grid grid-cols-[70px_minmax(0,1fr)_64px] items-center gap-2.5 text-xs"
                  >
                    <span className="text-ink-3">{EVENTS[item.event] ?? item.event}</span>
                    <span
                      className="pane-well relative block h-3.5 rounded-full"
                      role="img"
                      aria-label={`${item.event}: gain ${gain(bounds.value, 3)}, interval ${gain(bounds.low ?? bounds.value, 3)} to ${gain(bounds.high ?? bounds.value, 3)}`}
                    >
                      <span className="absolute top-0.5 bottom-0.5 left-1/2 w-px bg-[#4e5058]" />
                      <span
                        className="grow absolute top-1 h-1.5 rounded-full bg-[linear-gradient(90deg,#3a8bff,#ff8a4c)]"
                        style={{
                          left: `${mx(bounds.low ?? bounds.value)}%`,
                          width: `${mx(bounds.high ?? bounds.value) - mx(bounds.low ?? bounds.value)}%`,
                        }}
                      />
                      <span
                        className="absolute top-0.5 h-2.5 w-[3px] -translate-x-1/2 rounded-sm bg-white"
                        style={{ left: `${mx(bounds.value)}%` }}
                      />
                    </span>
                    <span className="figure text-right">{gain(bounds.value, 3)}</span>
                  </div>
                );
              })}
              <div className="flex justify-between text-[10.5px] text-mute">
                <span>market better</span>
                <span>model better</span>
              </div>
            </section>
          )}
        </div>
      </div>
    </Frame>
  );
}
