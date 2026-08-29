import { NotBuilt } from "@/components/not-built";

export const metadata = { title: "Forecast · Pit Advisor" };

export default function ForecastPage() {
  return (
    <div className="pt-2">
      <h1 className="legend mb-10 text-4xl text-lume sm:text-5xl">Forecast</h1>
      <NotBuilt
        title="No forecast exists yet"
        what="There is no finishing-position forecast on this dial, because none has been fitted. Rather than render a placeholder, the page says so: a number that has not been backtested has no business appearing beside numbers that have."
        produces="A Monte Carlo race simulation over starts, tyre degradation, overtaking, safety cars and retirements, emitted as forecast_view with a distribution per driver rather than a single finishing position."
        gate="It ships only if it beats both baselines, grid position and championship standings, on multiclass log loss and Brier over a time-forward holdout of at least sixty races, with race-level bootstrap intervals. If it loses, this page will say that instead."
      />
    </div>
  );
}
