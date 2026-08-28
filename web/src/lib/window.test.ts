import { describe, expect, it } from "vitest";
import { window } from "./window";

describe("window", () => {
  it("frames the bulk rather than the stray", () => {
    const bulk = Array.from({ length: 50 }, (_, index) => 1 + index * 0.02);
    const framed = window([...bulk, 56.7]);
    expect(framed.high).toBeLessThan(10);
  });

  it("always keeps the benchmark in frame", () => {
    const framed = window([2.1, 2.4, 2.6]);
    expect(framed.low).toBeLessThanOrEqual(0);
  });

  it("survives a single sample", () => {
    expect(window([1.5]).high).toBeGreaterThan(0);
  });
});
