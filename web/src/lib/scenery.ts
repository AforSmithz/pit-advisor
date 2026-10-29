import type { SceneryView } from "./schemas";

export const ELEVATION = "ops:elevation";
export const SETTLED = "ops:settled";
// a page hands the scene marks to raise on its turns, and either side can pick one
export const TURN_LAYER = "ops:turn-layer";
export const TURN_PICK = "ops:turn-pick";

export type TurnMark = { number: number; count: number; tone: string };

export function turnIndices(track: SceneryView["track"]): Map<number, number> {
  return new Map(track.turns.map((t) => [t.number, Math.round(t.at_m / track.step_m) % track.points.length]));
}

// marks are sized by the square root, so one turn with a hundred deletions does not flatten the rest
export function markHeight(count: number, most: number, tallest: number): number {
  if (count <= 0 || most <= 0) return 0;
  return tallest * (0.12 + 0.88 * Math.sqrt(count / most));
}

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

export type Pose = "overview" | "straight" | "overhead" | "corner" | "drift";

export function poseFor(path: string): Pose {
  if (path === "/") return "overview";
  if (path.startsWith("/forecast")) return "straight";
  if (path.startsWith("/circuits")) return "overhead";
  if (path.startsWith("/stewards")) return "corner";
  return "drift";
}

export type Shot = { position: Vec3; target: Vec3 };

function orbit(centre: Vec3, azimuth: number, elevation: number, distance: number): Vec3 {
  return [
    centre[0] + Math.sin(azimuth) * Math.cos(elevation) * distance,
    Math.sin(elevation) * distance,
    centre[2] + Math.cos(azimuth) * Math.cos(elevation) * distance,
  ];
}

// the straight shot sits high behind the start line, looking past it to the middle of the track so the whole lap is in frame
export function shot(pose: Pose, track: SceneryView["track"], frame: Framing, exaggerate: number): Shot {
  const { centre, distance } = frame;
  if (pose === "overview") return { position: frame.rest, target: centre };
  if (pose === "overhead") return { position: [centre[0], distance * 0.95, centre[2] + 1], target: centre };
  if (pose === "corner") return { position: orbit(centre, 2.4, 1.0, distance * 0.95), target: centre };
  if (pose === "drift") return { position: orbit(centre, -0.5, 0.75, distance * 1.05), target: centre };
  const { points, step_m } = track;
  const at = (i: number) => {
    const [x, y, z] = points[i % points.length]!;
    return world(x, y, z * exaggerate + 12);
  };
  const start = at(0);
  const ahead = at(Math.round(250 / step_m));
  const dx = ahead[0] - start[0], dz = ahead[2] - start[2];
  const len = Math.hypot(dx, dz) || 1;
  const fx = dx / len, fz = dz / len;
  const back = distance * 0.8, side = distance * 0.22;
  return {
    position: [start[0] - fx * back - fz * side, start[1] + distance * 0.75, start[2] - fz * back + fx * side],
    target: [start[0] * 0.3 + centre[0] * 0.7, start[1], start[2] * 0.3 + centre[2] * 0.7],
  };
}
