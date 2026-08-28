import type { Estimate } from "./schemas";

export type Scale = { min: number; max: number; ticks: number[] };

const STEPS = [0.05, 0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20, 25, 50, 100];

function step(span: number, target: number): number {
  const raw = span / target;
  return STEPS.find((candidate) => candidate >= raw) ?? STEPS[STEPS.length - 1]!;
}

export function scaleFor(estimates: (Estimate | null)[], targetTicks = 6): Scale {
  const bounds = estimates.flatMap((item) => (item ? [item.low, item.high, item.value] : []));
  if (bounds.length === 0) return { min: 0, max: 1, ticks: [0, 1] };
  const low = Math.min(...bounds, 0);
  const high = Math.max(...bounds, 0);
  const span = high - low || 1;
  const size = step(span, targetTicks);
  const min = Math.floor(low / size) * size;
  const max = Math.ceil(high / size) * size;
  const ticks: number[] = [];
  // floating point drift shows up as a duplicated final tick without the epsilon
  for (let at = min; at <= max + size / 1000; at += size) {
    ticks.push(Number(at.toFixed(6)));
  }
  return { min, max, ticks };
}

export function position(value: number, scale: Scale): number {
  const span = scale.max - scale.min || 1;
  return ((value - scale.min) / span) * 100;
}
