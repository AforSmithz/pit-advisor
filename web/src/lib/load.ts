import { access, readFile } from "node:fs/promises";
import path from "node:path";
import type { ZodType } from "zod";
import {
  briefView,
  calibrationView,
  debriefView,
  driverView,
  forecastView,
  historyView,
  lineageView,
  sceneryView,
  sprintView,
  stewardsView,
  trackView,
  upgradesView,
  weekendView,
} from "./schemas";

const dataDir = path.join(process.cwd(), "public", "data");

async function read<T>(name: string, schema: ZodType<T>): Promise<T> {
  let raw: string;
  try {
    raw = await readFile(path.join(dataDir, `${name}.json`), "utf8");
  } catch {
    throw new Error(
      `web/public/data/${name}.json is missing. Run 'just web-data' to copy the emitted views in.`,
    );
  }
  const parsed = schema.safeParse(JSON.parse(raw));
  if (!parsed.success) {
    throw new Error(`${name}.json does not match the view contract: ${parsed.error.message}`);
  }
  return parsed.data;
}

// a view newer than the lake it is built against builds without its panel rather than failing
async function optional<T>(name: string, schema: ZodType<T>): Promise<T | null> {
  try {
    await access(path.join(dataDir, `${name}.json`));
  } catch {
    return null;
  }
  return read(name, schema);
}

export const loadWeekend = () => read("weekend_view", weekendView);
export const loadDriver = () => read("driver_view", driverView);
export const loadTrack = () => read("track_view", trackView);
export const loadForecast = () => read("forecast_view", forecastView);
export const loadCalibration = () => read("calibration_view", calibrationView);
export const loadBrief = () => read("brief_view", briefView);
export const loadSprint = () => optional("sprint_view", sprintView);
export const loadHistory = () => optional("history_view", historyView);
export const loadStewards = () => optional("stewards_view", stewardsView);
export const loadUpgrades = () => optional("upgrades_view", upgradesView);
export const loadDebrief = () => optional("debrief_view", debriefView);
export const loadLineage = () => optional("lineage_view", lineageView);
export const loadScenery = (circuitId: string) => optional(`scenery/${circuitId}`, sceneryView);
