import { NotBuilt } from "@/components/not-built";

export const metadata = { title: "Calibration · Pit Advisor" };

export default function CalibrationPage() {
  return (
    <div className="pt-2">
      <h1 className="legend mb-10 text-4xl text-lume sm:text-5xl">Calibration</h1>
      <NotBuilt
        title="Nothing to calibrate yet"
        what="This page leads the dashboard the day a forecast exists, because a reliability curve is the only honest way to read a probability. Until something is being predicted there is nothing to plot, and an empty diagonal would be decoration."
        produces="A reliability curve with bootstrap bands, expected calibration error with its interval, and the same log loss and Brier figures for every baseline, all read from calibration_view."
        gate="The curve has to sit inside bootstrap noise of the diagonal. A curve that looks perfect on the training window means leakage, and is treated as a defect rather than a result."
      />
    </div>
  );
}
