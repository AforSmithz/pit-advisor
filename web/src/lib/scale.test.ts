import { describe, expect, it } from "vitest";
import { position, scaleFor } from "./scale";

const at = (value: number, low: number, high: number) => ({ value, low, high, samples: 10 });

describe("scaleFor", () => {
  it("always contains the benchmark", () => {
    const scale = scaleFor([at(2.4, 2.1, 2.7)]);
    expect(scale.min).toBe(0);
    expect(scale.max).toBeGreaterThanOrEqual(2.7);
  });

  it("covers every interval end, not just the estimates", () => {
    const scale = scaleFor([at(0.2, -1.9, 2.4), at(0.1, 0, 0.2)]);
    expect(scale.min).toBeLessThanOrEqual(-1.9);
    expect(scale.max).toBeGreaterThanOrEqual(2.4);
  });

  it("survives a group where every estimate is missing", () => {
    expect(scaleFor([null, null]).ticks.length).toBeGreaterThan(1);
  });

  it("does not emit a duplicated last tick", () => {
    const ticks = scaleFor([at(0.3, 0.1, 0.5)]).ticks;
    expect(new Set(ticks).size).toBe(ticks.length);
  });
});

describe("position", () => {
  it("maps the domain onto nought to a hundred", () => {
    const scale = { min: -1, max: 1, ticks: [-1, 0, 1] };
    expect(position(-1, scale)).toBe(0);
    expect(position(0, scale)).toBe(50);
    expect(position(1, scale)).toBe(100);
  });
});
