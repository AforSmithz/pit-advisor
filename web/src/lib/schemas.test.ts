import { describe, expect, it } from "vitest";

import { calibrationView, sprintView } from "./schemas";

const bounds = { value: 0.2, low: 0.1, high: 0.3, races: 36, draws: 2000 };

function view(extra: Record<string, unknown> = {}) {
  return {
    view: "calibration_view",
    schema_version: "1.1.0",
    generated_at: "2026-10-10T00:00:00Z",
    run_id: "run-1",
    from_season: 2021,
    holdout: 60,
    paths: 4000,
    seed: 11,
    field: 22,
    model_name: "simulation",
    events: ["win", "podium", "points"],
    scored: [
      {
        name: "simulation",
        rows: 1231,
        races: 60,
        log_loss: bounds,
        brier: bounds,
        calibration: { win: 0.006 },
        curves: {},
        ...extra,
      },
    ],
    per_race: [],
    beats_baselines: true,
    separated_from: ["grid"],
    paired: [{ baseline: "grid", log_loss_gain: bounds, brier_gain: bounds }],
    assumptions: [],
  };
}

describe("calibrationView", () => {
  it("reads a view written before the event scores existed", () => {
    const parsed = calibrationView.parse(view());
    expect(parsed.scored[0]?.events).toEqual({});
    expect(parsed.paired[0]?.events).toEqual({});
  });

  it("carries the event scores and their split when they are there", () => {
    const split = {
      log_loss: bounds,
      brier: bounds,
      reliability: 0.001,
      resolution: 0.02,
      uncertainty: 0.04,
    };
    const parsed = calibrationView.parse(view({ events: { win: split } }));
    expect(parsed.scored[0]?.events.win?.resolution).toBe(0.02);
  });

  it("refuses an event score with no interval shape", () => {
    expect(() =>
      calibrationView.parse(view({ events: { win: { log_loss: 0.2 } } })),
    ).toThrow();
  });
});

const event = {
  season: 2026,
  round: 19,
  circuit_id: "americas",
  race_name: "United States Grand Prix",
  race_date: "2026-10-25",
  start_utc: "2026-10-25T19:00:00Z",
};

const head = {
  view: "sprint_view",
  schema_version: "1.1.0",
  generated_at: "2026-10-24T00:40:00Z",
  run_id: "run-1",
  event,
};

describe("sprintView", () => {
  it("reads a weekend without a sprint", () => {
    const parsed = sprintView.parse({
      ...head,
      sprint_utc: null,
      state: "no_sprint",
      forecast: null,
    });
    expect(parsed.forecast).toBeNull();
  });

  it("refuses a state the backend does not emit", () => {
    expect(
      sprintView.safeParse({
        ...head,
        sprint_utc: null,
        state: "maybe",
        forecast: null,
      }).success,
    ).toBe(false);
  });
});
