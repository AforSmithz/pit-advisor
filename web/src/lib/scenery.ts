import type { SceneryView } from "./schemas";

export const ELEVATION = "ops:elevation";
export const SETTLED = "ops:settled";

export type Vec3 = [number, number, number];

// the track frame is metres with x east, y north and z up; three wants y up, so north runs to -z
export function world(x: number, y: number, height: number): Vec3 {
  return [x, height, -y];
}

export type Ribbon = {
  centre: Vec3[];
  left: Vec3[];
  right: Vec3[];
  across: Vec3[];
};

export function ribbon(points: SceneryView["track"]["points"], half: number, lift: number, exaggerate: number): Ribbon {
  const n = points.length;
  const centre = points.map(([x, y, z]) => world(x, y, z * exaggerate + lift));
  const across = centre.map((_, i) => {
    const a = centre[(i - 1 + n) % n]!, b = centre[(i + 1) % n]!;
    const tx = b[0] - a[0], tz = b[2] - a[2];
    const len = Math.hypot(tx, tz) || 1;
    return [-tz / len, 0, tx / len] as Vec3;
  });
  const side = (sign: number) =>
    centre.map((p, i) => [p[0] + across[i]![0] * half * sign, p[1], p[2] + across[i]![2] * half * sign] as Vec3);
  return { centre, left: side(1), right: side(-1), across };
}

export function perimeter(gx: number, gy: number): number[] {
  const ring: number[] = [];
  for (let c = 0; c < gx; c++) ring.push(c);
  for (let r = 1; r < gy; r++) ring.push(r * gx + gx - 1);
  for (let c = gx - 2; c >= 0; c--) ring.push((gy - 1) * gx + c);
  for (let r = gy - 2; r > 0; r--) ring.push(r * gx);
  return ring;
}

export function sectorIndices(track: SceneryView["track"]): number[] {
  return track.sectors_m.map((m) => Math.round(m / track.step_m) % track.points.length);
}

export type Framing = { centre: Vec3; rest: Vec3; overhead: Vec3; distance: number };

// portrait screens see less width at the same distance, so the camera backs off
export function framing(points: SceneryView["track"]["points"], aspect: number): Framing {
  let minx = Infinity, maxx = -Infinity, miny = Infinity, maxy = -Infinity;
  for (const [x, y] of points) {
    minx = Math.min(minx, x); maxx = Math.max(maxx, x);
    miny = Math.min(miny, y); maxy = Math.max(maxy, y);
  }
  const span = Math.hypot(maxx - minx, maxy - miny);
  const distance = span * 1.9 * Math.max(1, 0.75 / aspect);
  const centre = world((minx + maxx) / 2, (miny + maxy) / 2, 0);
  const azimuth = -0.5, elevation = 0.9;
  const rest: Vec3 = [
    centre[0] + Math.sin(azimuth) * Math.cos(elevation) * distance,
    Math.sin(elevation) * distance,
    centre[2] + Math.cos(azimuth) * Math.cos(elevation) * distance,
  ];
  return { centre, rest, overhead: [centre[0], distance, centre[2] + 1], distance };
}
