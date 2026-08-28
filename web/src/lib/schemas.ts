import { z } from "zod";

export const estimate = z.object({
  value: z.number(),
  low: z.number(),
  high: z.number(),
  samples: z.number().int(),
});

const nullableEstimate = estimate.nullable();

const viewHead = {
  view: z.string(),
  schema_version: z.string(),
  generated_at: z.string(),
  run_id: z.string(),
};

export const eventContext = z.object({
  season: z.number().int(),
  round: z.number().int(),
  circuit_id: z.string(),
  race_name: z.string(),
  race_date: z.string(),
  start_utc: z.string().nullable(),
});

export const coverage = z.object({
  races_in_lake: z.number().int(),
  sessions_fitted: z.number().int(),
  sessions_skipped: z.number().int(),
  skips: z.record(z.string(), z.number().int()),
  dry_sessions: z.number().int(),
  wet_sessions: z.number().int(),
  clean_laps: z.number().int(),
  total_laps: z.number().int(),
  exclusion_rate: z.number(),
  drivers_rated: z.number().int(),
  quali_events: z.number().int(),
  exclusions: z.record(z.string(), z.number().int()),
});

export const scenarioWeights = z.object({
  dry: z.number(),
  mixed: z.number(),
  wet: z.number(),
  hours: z.number().int(),
  is_forecast: z.boolean(),
  snapshot_at: z.string(),
  expected_mm: z.number(),
  wettest_hour: z.number(),
  driest_hour: z.number(),
});

export const neighbour = z.object({
  circuit_id: z.string(),
  similarity: z.number(),
});

export const paceSample = z.object({
  season: z.number().int(),
  round: z.number().int(),
  race_date: z.string(),
  circuit_id: z.string(),
  regime: z.string(),
  percent_off_benchmark: z.number(),
});

export const teammateSample = z.object({
  season: z.number().int(),
  round: z.number().int(),
  race_date: z.string(),
  circuit_id: z.string(),
  teammate: z.string(),
  delta: z.number(),
});

export const weekendView = z
  .object({
    ...viewHead,
    as_of: z.string(),
    event: eventContext,
    drivers: z.array(
      z.object({
        driver_code: z.string(),
        constructor_id: z.string(),
        form: nullableEstimate,
        form_component: z.number().int().nullable(),
        quali_race: nullableEstimate,
        wet: nullableEstimate,
      }),
    ),
    teams: z.array(
      z.object({
        constructor_id: z.string(),
        track_fit_regression: nullableEstimate,
        track_fit_similarity: nullableEstimate,
        estimators_disagree: z.boolean(),
        dnf_per_lap: nullableEstimate,
        wet: nullableEstimate,
      }),
    ),
    weather: scenarioWeights.nullable(),
    neighbours: z.array(neighbour),
    coverage,
    cause_coverage: z.number(),
  })
  .strict();

export const driverView = z
  .object({
    ...viewHead,
    as_of: z.string(),
    event: eventContext,
    half_life_events: z.number(),
    drivers: z.array(
      z.object({
        driver_code: z.string(),
        constructor_id: z.string(),
        form: nullableEstimate,
        form_component: z.number().int().nullable(),
        quali_race: nullableEstimate,
        wet: nullableEstimate,
        pace: z.array(paceSample),
        teammate: z.array(teammateSample),
      }),
    ),
  })
  .strict();

export const trackView = z
  .object({
    ...viewHead,
    as_of: z.string(),
    event: eventContext,
    profile: z.object({
      circuit_id: z.string(),
      length_km: z.number(),
      corners: z.number().int(),
      direction: z.string(),
      altitude_m: z.number(),
      reprofiled: z.string().nullable(),
      demand: z.record(z.string(), z.number()),
    }),
    neighbours: z.array(neighbour),
    teams: z.array(
      z.object({
        constructor_id: z.string(),
        regression: nullableEstimate,
        similarity: nullableEstimate,
        disagree: z.boolean(),
        history: z.array(paceSample),
      }),
    ),
    dropped_reprofiled: z.number().int(),
  })
  .strict();

export const pipelineView = z
  .object({
    ...viewHead,
    layer: z.string(),
    healthy: z.boolean(),
    tables: z.array(
      z.object({
        table: z.string(),
        source: z.string(),
        status: z.enum(["ok", "warn", "fail"]),
        detail: z.string(),
      }),
    ),
    quarantine: z.array(
      z.object({
        table: z.string(),
        reason: z.string(),
        rows: z.number().int(),
        explained: z.boolean(),
      }),
    ),
    diagnostics: z.array(
      z.object({
        name: z.string(),
        table: z.string(),
        value: z.number().int(),
        detail: z.string(),
      }),
    ),
    quota: z.array(
      z.object({
        name: z.string(),
        capacity: z.number().int(),
        tokens_left: z.number(),
        refill_per_second: z.number(),
        measured_at: z.string(),
      }),
    ),
  })
  .strict();

export type Estimate = z.infer<typeof estimate>;
export type EventContext = z.infer<typeof eventContext>;
export type Coverage = z.infer<typeof coverage>;
export type Neighbour = z.infer<typeof neighbour>;
export type PaceSample = z.infer<typeof paceSample>;
export type TeammateSample = z.infer<typeof teammateSample>;
export type ScenarioWeights = z.infer<typeof scenarioWeights>;
export type WeekendView = z.infer<typeof weekendView>;
export type DriverView = z.infer<typeof driverView>;
export type TrackView = z.infer<typeof trackView>;
export type PipelineView = z.infer<typeof pipelineView>;
