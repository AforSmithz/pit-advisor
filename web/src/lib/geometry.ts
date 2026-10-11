// drawing helpers. they place shipped numbers on a dial or an axis and compute nothing new

export function polar(cx: number, cy: number, r: number, degrees: number): [number, number] {
  const a = ((degrees - 90) * Math.PI) / 180;
  return [cx + r * Math.cos(a), cy + r * Math.sin(a)];
}

export function arc(cx: number, cy: number, r: number, from: number, to: number): string {
  const [x0, y0] = polar(cx, cy, r, from);
  const [x1, y1] = polar(cx, cy, r, to);
  const large = to - from > 180 ? 1 : 0;
  return `M${x0.toFixed(2)} ${y0.toFixed(2)} A${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`;
}

export function arcLength(r: number, from: number, to: number): number {
  return (r * (to - from) * Math.PI) / 180;
}

/** Maps a value onto [0, 1] within a domain, clamped, for placing it along an axis. */
export function along(value: number, low: number, high: number): number {
  if (high === low) return 0.5;
  return Math.min(1, Math.max(0, (value - low) / (high - low)));
}

export function smoothPath(points: [number, number][]): string {
  if (points.length === 0) return "";
  const [start] = points as [[number, number]];
  let path = `M${start[0].toFixed(1)} ${start[1].toFixed(1)}`;
  for (let i = 0; i < points.length - 1; i++) {
    const p1 = points[i]!;
    const p2 = points[i + 1]!;
    const p0 = points[i - 1] ?? p1;
    const p3 = points[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) / 6;
    const c1y = p1[1] + (p2[1] - p0[1]) / 6;
    const c2x = p2[0] - (p3[0] - p1[0]) / 6;
    const c2y = p2[1] - (p3[1] - p1[1]) / 6;
    path += ` C${c1x.toFixed(1)} ${c1y.toFixed(1)} ${c2x.toFixed(1)} ${c2y.toFixed(1)} ${p2[0].toFixed(1)} ${p2[1].toFixed(1)}`;
  }
  return path;
}

const RAMP: [number, [number, number, number]][] = [
  [0, [29, 21, 48]],
  [0.22, [90, 43, 184]],
  [0.45, [192, 63, 208]],
  [0.65, [255, 95, 109]],
  [0.83, [255, 154, 60]],
  [1, [255, 210, 90]],
];

/** The heat ramp, violet through coral to yellow, for a share between 0 and 1. */
export function heat(share: number): string {
  const t = Math.min(1, Math.max(0, share));
  for (let i = 1; i < RAMP.length; i++) {
    const [stop, to] = RAMP[i]!;
    if (t <= stop) {
      const [start, from] = RAMP[i - 1]!;
      const k = (t - start) / (stop - start);
      const mix = from.map((channel, j) => Math.round(channel + (to[j]! - channel) * k));
      return `rgb(${mix.join(",")})`;
    }
  }
  return "rgb(255,210,90)";
}
