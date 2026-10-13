export type Box = { left: number; top: number; right: number; bottom: number };

export function hash(a: number, b: number, c: number): number {
  let h = Math.imul(a ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(b + 0x632be5ab, 0xc2b2ae35) ^ Math.imul(c + 0x2c1b3c6d, 0x27d4eb2f);
  h ^= h >>> 15;
  h = Math.imul(h, 0x2c1b3c6d);
  h ^= h >>> 12;
  return (h >>> 0) / 4294967296;
}

// tilted so the sweep climbs left to right in stairs, with lumps on the way
export function skyline(count: number, seed: number): number[] {
  const a = seed * 6.283;
  const b = seed * 17.3;
  return Array.from({ length: count }, (_, c) => {
    const tilt = 1 - c / Math.max(1, count - 1);
    const lump = 0.5 + 0.32 * Math.sin(c * 0.11 + a) + 0.18 * Math.sin(c * 0.29 + b);
    return Math.min(1, Math.max(0, 0.6 * tilt + 0.4 * lump));
  });
}

// a column that lags more starts later but still lands at progress 1
export function lit(rows: number, progress: number, lag: number, spread = 1.1): number {
  const level = progress * (1 + spread) - spread * lag;
  return Math.max(0, Math.min(rows, Math.floor(level * rows)));
}

export function ease(t: number): number {
  const x = Math.min(1, Math.max(0, t));
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
}

export function inset(box: Box, width: number, height: number, radius = 0): string {
  return `inset(${box.top}px ${width - box.right}px ${height - box.bottom}px ${box.left}px round ${radius}px)`;
}

export function rgba(hex: string, alpha: number): string {
  const value = Number.parseInt(hex.replace("#", ""), 16);
  return `rgba(${(value >> 16) & 255}, ${(value >> 8) & 255}, ${value & 255}, ${alpha})`;
}
