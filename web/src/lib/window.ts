export type Window = { low: number; high: number; ticks: number[] };

function quantile(sorted: number[], share: number): number {
  const at = (sorted.length - 1) * share;
  const below = Math.floor(at);
  const above = Math.ceil(at);
  const lower = sorted[below] ?? 0;
  const upper = sorted[above] ?? lower;
  return lower + (upper - lower) * (at - below);
}

// one wet race 50% off the benchmark flattens five seasons of dry pace into a line,
// so the plate is framed on the bulk and the strays are drawn on the edge
export function window(values: number[]): Window {
  if (values.length === 0) return { low: 0, high: 1, ticks: [0, 1] };
  const sorted = [...values].sort((a, b) => a - b);
  const inner = { low: quantile(sorted, 0.02), high: quantile(sorted, 0.98) };
  const span = inner.high - inner.low || 1;
  const low = Math.min(inner.low - span * 0.1, 0);
  const high = Math.max(inner.high + span * 0.1, 0);
  const mid = (low + high) / 2;
  return { low, high, ticks: [low, mid, high].map((tick) => Number(tick.toFixed(2))) };
}
