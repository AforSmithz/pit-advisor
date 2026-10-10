import { access, readFile } from "node:fs/promises";
import path from "node:path";
import type { ZodType } from "zod";
import {
  briefView,
  calibrationView,
  costView,
  driverView,
  forecastView,
  pipelineView,
  sprintView,
  trackView,
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
    throw new Error(
      `${name}.json does not match the view contract: ${parsed.error.message}`,
    );
  }
  return parsed.data;
}

// the sprint view arrived after the others, so a lake that predates it builds without the plate
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
export const loadPipeline = () => read("pipeline_view", pipelineView);
export const loadForecast = () => read("forecast_view", forecastView);
export const loadCalibration = () => read("calibration_view", calibrationView);
export const loadCost = () => read("cost_view", costView);
export const loadBrief = () => read("brief_view", briefView);
export const loadSprint = () => optional("sprint_view", sprintView);
