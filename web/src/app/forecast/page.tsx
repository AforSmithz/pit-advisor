import { Scenario } from "@/components/bars";
import { Order, OrderRuler, Spread } from "@/components/forecast";
import { Engraved, Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { assumption, isoDate, label } from "@/lib/format";
import { loadForecast } from "@/lib/load";

export const metadata = { title: "Forecast · Pit Advisor" };

export default async function ForecastPage() {
  const view = await loadForecast();
  const field = view.drivers.length;
  const weights = Object.fromEntries(
    view.scenarios.map((item) => [item.scenario, item.weight]),
  );
  const evidence = view.evidence;
  const ordered = evidence
    ? Object.entries(evidence.log_loss).sort((a, b) => a[1].value - b[1].value)
    : [];

  return (
    <div className="pt-2">
      <div className="flex flex-wrap items-end justify-between gap-x-10 gap-y-4 border-b border-engrave-lit pb-5">
        <div>
          <h1 className="legend text-4xl text-lume sm:text-5xl">
            {view.event.race_name}
          </h1>
          <p className="engraved mt-2">
            {label(view.event.circuit_id)} · round {view.event.round} ·{" "}
            {view.laps} laps · {view.paths.toLocaleString("en-GB")} simulated
            races
          </p>
        </div>
        <dl className="flex flex-wrap gap-x-8 gap-y-2 text-right">
          <div>
            <dt className="engraved">as of</dt>
            <dd className="figure text-sm text-lume">{isoDate(view.as_of)}</dd>
          </div>
          <div>
            <dt className="engraved">weather</dt>
            <dd className="figure text-sm text-lume">
              {view.weights_are_forecast ? "forecast" : "climatology"}
            </dd>
          </div>
        </dl>
      </div>

      {evidence && (
        <p className="mt-6 max-w-[70ch] text-sm leading-relaxed text-lume-dim">
          Held out over {evidence.races} time-forward races from{" "}
          {evidence.from_season}, this simulation scored{" "}
          {evidence.log_loss.simulation?.value.toFixed(4)} of multiclass log
          loss against {evidence.log_loss.grid?.value.toFixed(4)} for predicting
          from the grid alone.{" "}
          {evidence.separated_from.length
            ? `Resampled over the same races it is clear of ${evidence.separated_from
                .map((name: string) => name.replace(/_/g, " "))
                .join(
                  " and ",
                )}; against anything else the difference sits inside bootstrap noise.`
            : "No baseline is separated from it by this holdout, and the numbers below are published anyway rather than quietly withdrawn."}
        </p>
      )}

      <div className="mt-10 grid gap-10 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <div className="flex flex-col gap-10">
          <Plate
            title="Where the paths finish"
            note="The split mark is the mean finishing place over every path. The bar spans the middle eight paths in ten, and the dashed rule is the grid slot the car starts from."
          >
            <OrderRuler field={field} />
            {view.drivers.map((driver) => (
              <Order key={driver.driver_code} driver={driver} field={field} />
            ))}
            <Provenance
              view={view.view}
              runId={view.run_id}
              asOf={view.as_of}
              stands={`${view.paths.toLocaleString("en-GB")} paths a scenario, blended by the weather weights`}
            />
          </Plate>

          <Plate
            title="The whole distribution"
            note="One cell per finishing place, darkest where the paths piled up. The figure on the right is how often the car was still running at the end."
          >
            {view.drivers.map((driver) => (
              <Spread key={driver.driver_code} driver={driver} field={field} />
            ))}
          </Plate>
        </div>

        <aside className="flex flex-col gap-10">
          <Plate
            title="Weather"
            note={
              view.weights_are_forecast
                ? "Taken from the forecast covering the session window."
                : "This race has already been run, so its recorded weather is what happened rather than a forecast. Using it would tell the simulation whether it rained, so the weights are this circuit's history instead."
            }
          >
            <Scenario
              dry={weights.dry ?? 0}
              mixed={weights.mixed ?? 0}
              wet={weights.wet ?? 0}
            />
          </Plate>

          {evidence && (
            <Plate
              title="Evidence"
              note="Multiclass log loss over the holdout. Lower is better."
            >
              <ul>
                {ordered.map(([name, score]) => (
                  <li
                    key={name}
                    className="flex items-baseline justify-between gap-4 border-t border-engrave py-2"
                  >
                    <span
                      className={`text-sm tracking-plate ${
                        name === "simulation" ? "text-lume" : "text-steel"
                      }`}
                    >
                      {label(name)}
                    </span>
                    <span className="text-right">
                      <span className="figure block text-sm text-lume">
                        {score.value.toFixed(4)}
                      </span>
                      <span className="engraved block tabular-nums">
                        {score.low === null || score.high === null
                          ? "not resampled"
                          : `${score.low.toFixed(3)} to ${score.high.toFixed(3)}`}
                      </span>
                    </span>
                  </li>
                ))}
              </ul>
            </Plate>
          )}

          <Plate
            title="Assumptions"
            note="Every one of these was fitted on races before this one, not chosen."
          >
            <dl>
              {view.assumptions.map((item) => (
                <div key={item.name} className="border-t border-engrave py-2">
                  <div className="flex items-baseline justify-between gap-4">
                    <dt className="engraved">{item.name.replace(/_/g, " ")}</dt>
                    <dd className="figure text-sm text-lume">
                      {assumption(item.name, item.value)}
                    </dd>
                  </div>
                  <p className="engraved mt-1 normal-case tracking-normal text-lume-dim">
                    {item.detail}
                  </p>
                </div>
              ))}
            </dl>
          </Plate>

          <Plate title="Contract">
            <dl>
              <Engraved term="view">{view.view}</Engraved>
              <Engraved term="schema">{view.schema_version}</Engraved>
              <Engraved term="paths">
                {view.paths.toLocaleString("en-GB")}
              </Engraved>
            </dl>
          </Plate>
        </aside>
      </div>
    </div>
  );
}
