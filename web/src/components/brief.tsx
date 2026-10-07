import { Plate } from "@/components/plate";
import { Provenance } from "@/components/provenance";
import { label, percentOf, signed } from "@/lib/format";
import type { BriefView } from "@/lib/schemas";

function places(low: number, high: number): string {
  return low === high ? `P${low}` : `P${low} to P${high}`;
}

function names(ids: string[]): string {
  return ids.map(label).join(", ");
}

// every sentence here is made of fields the view carries. the page joins words around them and
// never works anything out, so a brief can not say a number no gated view said first
export function Brief({ brief }: { brief: BriefView }) {
  const weather = brief.weather;
  const forecast = brief.sources.find((source) => source.view === "forecast_view");
  const evidence =
    brief.beats_baselines === null
      ? "No backtest travelled with this forecast."
      : brief.separated_from.length
        ? `The backtest separates the simulation from ${brief.separated_from
            .map((name) => name.replace(/_/g, " "))
            .join(" and ")}; against the rest it sits inside bootstrap noise.`
        : "The backtest does not separate the simulation from any baseline.";

  return (
    <Plate
      title="Brief"
      note="Picked from the weekend, forecast and track views by code. An agent-written brief replaces it once the model quota opens."
      footer={
        <Provenance
          view={brief.view}
          runId={brief.run_id}
          asOf={forecast?.as_of ?? brief.generated_at}
          stands={`from ${brief.sources.map((source) => source.view).join(", ")}`}
        />
      }
    >
      <ul className="flex flex-col">
        <li className="border-t border-engrave py-3">
          <span className="engraved block">most likely to win</span>
          <span className="mt-1 block text-sm leading-relaxed text-lume">
            {brief.favourites.map((row, index) => (
              <span key={row.driver_code}>
                {index ? "; " : ""}
                <span className="figure">{row.driver_code}</span> {percentOf(row.win)} to win,{" "}
                {percentOf(row.podium)} podium, {places(row.position_low, row.position_high)}
                {row.grid === null ? "" : ` from grid ${row.grid}`}
              </span>
            ))}
            . Over {brief.simulated_paths.toLocaleString("en-GB")} simulated races
            {brief.grid_sampled
              ? ", each running its own qualifying first, since the grid is not set yet."
              : "."}
          </span>
          <span className="mt-1 block text-xs leading-relaxed text-lume-dim">{evidence}</span>
        </li>
        <li className="border-t border-engrave py-3">
          <span className="engraved block">in form</span>
          <span className="mt-1 block text-sm leading-relaxed text-lume">
            {brief.form_leaders.length
              ? brief.form_leaders.map((row, index) => (
                  <span key={row.driver_code}>
                    {index ? "; " : ""}
                    <span className="figure">{row.driver_code}</span>{" "}
                    <span className="figure">{signed(row.form.value)}%</span> [
                    <span className="figure">{signed(row.form.low)}</span>,{" "}
                    <span className="figure">{signed(row.form.high)}</span>]
                  </span>
                ))
              : "No driver in this season's field has a form rating yet."}
          </span>
        </li>
        <li className="border-t border-engrave py-3">
          <span className="engraved block">weather</span>
          <span className="mt-1 block text-sm leading-relaxed text-lume">
            {weather
              ? `${weather.is_forecast ? "Forecast" : "Observed"}: ${percentOf(weather.dry)} dry, ${percentOf(weather.mixed)} mixed, ${percentOf(weather.wet)} wet over ${weather.hours} hours, ${weather.expected_mm.toFixed(1)} mm expected.`
              : "No weather for this race in the lake yet. The forecast falls back to the circuit's history."}
          </span>
        </li>
        <li className="border-t border-engrave py-3">
          <span className="engraved block">the track</span>
          <span className="mt-1 block text-sm leading-relaxed text-lume">
            {label(brief.track.circuit_id)}, {brief.track.length_km.toFixed(3)} km and{" "}
            {brief.track.corners} corners
            {brief.track.neighbours.length
              ? `, plays most like ${names(brief.track.neighbours)}`
              : ""}
            .
            {brief.track.disagreements.length
              ? ` The two track-fit estimators disagree on ${names(brief.track.disagreements)}.`
              : ""}
          </span>
        </li>
      </ul>
    </Plate>
  );
}
