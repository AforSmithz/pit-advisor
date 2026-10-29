import { describe, expect, it } from "vitest";
import { framing, perimeter, poseFor, ribbon, sectorIndices, shot, world } from "./scenery";

describe("world", () => {
  it("puts north at negative z and height on y", () => {
    expect(world(3, 4, 5)).toEqual([3, 5, -4]);
  });
});

describe("ribbon", () => {
  const circle = Array.from({ length: 64 }, (_, i) => {
    const a = (i / 64) * Math.PI * 2;
    return [Math.cos(a) * 200, Math.sin(a) * 200, i % 2] as [number, number, number];
  });

  it("keeps both edges the full width apart", () => {
    const { left, right } = ribbon(circle, 10, 12, 3);
    for (let i = 0; i < circle.length; i++) {
      expect(Math.hypot(left[i]![0] - right[i]![0], left[i]![2] - right[i]![2])).toBeCloseTo(20);
    }
  });

  it("lifts the surface by the slab depth over the exaggerated height", () => {
    const { centre } = ribbon(circle, 10, 12, 3);
    expect(centre[1]![1]).toBe(15);
    expect(centre[0]![1]).toBe(12);
  });
});

describe("perimeter", () => {
  it("visits every edge node once", () => {
    const ring = perimeter(5, 4);
    expect(ring).toHaveLength(2 * (5 + 4) - 4);
    expect(new Set(ring).size).toBe(ring.length);
  });
});

describe("sectorIndices", () => {
  it("maps arc metres onto the resampled points", () => {
    const points = Array.from({ length: 100 }, () => [0, 0, 0] as [number, number, number]);
    expect(sectorIndices({ length_m: 500, step_m: 5, sectors_m: [150, 340], points })).toEqual([30, 68]);
  });
});

describe("framing", () => {
  const square = [[-500, -500, 0], [500, -500, 0], [500, 500, 0], [-500, 500, 0]] as [number, number, number][];

  it("backs off on a portrait screen", () => {
    expect(framing(square, 375 / 812).distance).toBeGreaterThan(framing(square, 1440 / 900).distance);
  });

  it("looks at the middle of the track", () => {
    expect(framing(square, 1.6).centre).toEqual([0, 0, -0]);
  });
});

describe("poseFor", () => {
  it("gives each page its own camera", () => {
    expect(poseFor("/")).toBe("overview");
    expect(poseFor("/forecast/")).toBe("straight");
    expect(poseFor("/circuits/")).toBe("overhead");
    expect(poseFor("/stewards/")).toBe("corner");
    expect(poseFor("/paddock/VER/")).toBe("drift");
    expect(poseFor("/record/")).toBe("drift");
  });
});

describe("shot", () => {
  // a straight run east from the origin, 5 m apart
  const points = Array.from({ length: 200 }, (_, i) => [i * 5, 0, 0] as [number, number, number]);
  const track = { length_m: 1000, step_m: 5, sectors_m: [], points };
  const frame = framing(points, 1.6);

  it("looks down the straight from behind the line", () => {
    const { position, target } = shot("straight", track, frame, 3);
    expect(target[0]).toBeCloseTo(250);
    expect(position[0]).toBeLessThan(0);
    expect(position[1]).toBeGreaterThan(target[1]);
  });

  it("keeps the overview where the home page rests", () => {
    expect(shot("overview", track, frame, 3)).toEqual({ position: frame.rest, target: frame.centre });
  });

  it("parks the ambient pages further out than the overview", () => {
    const far = shot("drift", track, frame, 3).position;
    const dist = (p: number[]) => Math.hypot(p[0]! - frame.centre[0], p[1]!, p[2]! - frame.centre[2]);
    expect(dist(far)).toBeGreaterThan(dist(frame.rest));
  });
});
