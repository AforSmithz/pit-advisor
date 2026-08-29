import { Key, Reliability, Series } from "@/components/curve";
import { Engraved, Plate } from "@/components/plate";
import { gain, label } from "@/lib/format";
import { loadCalibration } from "@/lib/load";

export const metadata = { title: "Calibration · Pit Advisor" };

export default async function CalibrationPage() {
  const view = await loadCalibration();
  const names = view.scored.map((item) => item.name);
  const ours = view.scored.find((item) => item.name === view.model_name);
  const separated = view.separated_from;

  return (
    <div className="pt-2">
      <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4 border-b border-engrave-lit pb-5">
        <div>
          <h1 className="legend text-4xl text-lume sm:text-5xl">Calibration</h1>
          <p className="engraved mt-2">
            {ours?.races ?? 0} time-forward races from {view.from_season} ·{" "}
            {ours?.rows.toLocaleString("en-GB") ?? 0} driver-races ·{" "}
            {view.paths.toLocaleString("en-GB")} paths a race
          </p>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-right">
          <div>
            <dt className="engraved">seed</dt>
            <dd className="figure text-sm text-lume">{view.seed}</dd>
          </div>
          <div>
            <dt className="engraved">classes</dt>
            <dd className="figure text-sm text-lume">{view.field}</dd>
          </div>
        </dl>
      </div>

      <p className="mt-6 max-w-[74ch] text-sm leading-relaxed text-lume-dim">
        The holdout is the last {view.holdout} races in the lake, scored one at
        a time with every fit behind the forecast seeing only what had happened
        when it was made. Intervals are bootstrapped over races rather than
        drivers, because twenty cars in one race share a track, a strategy and a
        safety car, and resampling them independently reports a spread several
        times tighter than the evidence supports.{" "}
        {separated.length
          ? `This holdout separates the simulation from ${separated
              .map((name: string) => name.replace(/_/g, " "))
              .join(
                " and ",
              )}; anything not on that list sits inside bootstrap noise.`
          : "This holdout separates the simulation from none of the baselines, which is the honest reading of it."}
      </p>

      <div className="mt-10 grid gap-10 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-10">
          <Plate
            title="Reliability"
            note="A probability that means what it says lands on the diagonal. Above it the forecast was shy, below it the forecast was bold."
            footer={
              <div className="mt-4">
                <Key models={names} />
              </div>
            }
          >
            <div className="grid gap-6 border-t border-engrave pt-4 sm:grid-cols-3">
              {view.events.map((event) => (
                <Reliability
                  key={event}
                  event={event}
                  scored={view.scored}
                  highlight={view.model_name}
                />
              ))}
            </div>
          </Plate>

          <Plate
            title="Scores"
            note="Multiclass over the classified finishing position, so a retirement is a class like any other. Lower is better on both."
          >
            <div className="grid grid-cols-[7rem_1fr_1fr] items-baseline gap-x-4 pb-1">
              <span className="engraved">model</span>
              <span className="engraved">log loss</span>
              <span className="engraved">brier</span>
            </div>
            {view.scored.map((model) => (
              <div
                key={model.name}
                className="grid grid-cols-[7rem_1fr_1fr] items-baseline gap-x-4 border-t border-engrave py-2.5"
              >
                <span
                  className={`text-sm tracking-plate ${
                    model.name === view.model_name ? "text-lume" : "text-steel"
                  }`}
                >
                  {label(model.name)}
                </span>
                {([model.log_loss, model.brier] as const).map(
                  (score, index) => (
                    <span key={index}>
                      <span className="figure block text-sm text-lume">
                        {score.value.toFixed(4)}
                      </span>
                      <span className="engraved block tabular-nums text-lume-dim">
                        {score.low === null || score.high === null
                          ? "not resampled"
                          : `${score.low.toFixed(4)} to ${score.high.toFixed(4)}`}
                      </span>
                    </span>
                  ),
                )}
              </div>
            ))}
          </Plate>

          <Plate
            title="Race by race"
            note="Log loss on each race in the holdout, in order. The spikes are the wet ones and the safety-car ones, and every model feels them together."
            footer={
              <div className="mt-2">
                <Key models={names} />
              </div>
            }
          >
            <div className="border-t border-engrave pt-3">
              <Series rows={view.per_race} models={names} />
            </div>
          </Plate>
        </div>

        <aside className="flex flex-col gap-10">
          <Plate
            title="Against each baseline"
            note="The same races, resampled together. An interval that straddles zero means this holdout did not separate them, whatever the point estimates say."
          >
            {view.paired.map((item) => (
              <div key={item.baseline} className="border-t border-engrave py-3">
                <div className="mb-1 flex items-baseline justify-between gap-4">
                  <span className="text-sm tracking-plate text-lume">
                    vs {label(item.baseline)}
                  </span>
                  <span className="engraved">
                    {separated.includes(item.baseline)
                      ? "separated"
                      : "inside noise"}
                  </span>
                </div>
                <dl className="flex gap-6">
                  {(
                    [
                      ["log loss", item.log_loss_gain],
                      ["brier", item.brier_gain],
                    ] as const
                  ).map(([name, score]) => (
                    <div key={name}>
                      <dt className="engraved">{name}</dt>
                      <dd className="figure text-sm text-lume">
                        {gain(score.value)}
                      </dd>
                      <dd className="engraved tabular-nums text-lume-dim">
                        {score.low === null || score.high === null
                          ? "—"
                          : `${gain(score.low)} to ${gain(score.high)}`}
                      </dd>
                    </div>
                  ))}
                </dl>
              </div>
            ))}
          </Plate>

          <Plate
            title="Calibration error"
            note="The count-weighted gap between what was promised and what happened, per derived event."
          >
            {view.scored.map((model) => (
              <div
                key={model.name}
                className="flex items-baseline justify-between gap-4 border-t border-engrave py-2"
              >
                <span
                  className={`text-sm tracking-plate ${
                    model.name === view.model_name ? "text-lume" : "text-steel"
                  }`}
                >
                  {label(model.name)}
                </span>
                <span className="figure text-sm text-lume-dim">
                  {view.events.map((event) => (
                    <span key={event} className="ml-3 tabular-nums">
                      {(model.calibration[event] ?? 0).toFixed(3)}
                    </span>
                  ))}
                </span>
              </div>
            ))}
            <p className="engraved mt-3 normal-case tracking-normal text-lume-dim">
              in the order {view.events.join(", ")}
            </p>
          </Plate>

          <Plate title="Contract">
            <dl>
              <Engraved term="view">{view.view}</Engraved>
              <Engraved term="schema">{view.schema_version}</Engraved>
              <Engraved term="run">{view.run_id}</Engraved>
              <Engraved term="holdout">{view.holdout}</Engraved>
            </dl>
          </Plate>
        </aside>
      </div>
    </div>
  );
}
