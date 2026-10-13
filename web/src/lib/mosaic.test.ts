import { describe, expect, it } from "vitest";
import { ease, hash, inset, lit, rgba, skyline } from "./mosaic";

describe("mosaic", () => {
  it("lights nothing at the start and every row at the end, whatever the lag", () => {
    for (const lag of [0, 0.3, 1]) {
      expect(lit(40, 0, lag)).toBe(0);
      expect(lit(40, 1, lag)).toBe(40);
    }
  });

  it("only ever adds rows as the sweep moves", () => {
    let last = 0;
    for (let p = 0; p <= 1; p += 0.01) {
      const rows = lit(40, p, 0.7);
      expect(rows).toBeGreaterThanOrEqual(last);
      last = rows;
    }
  });

  it("keeps the skyline and the hash inside the unit interval", () => {
    for (const lag of skyline(200, 0.42)) {
      expect(lag).toBeGreaterThanOrEqual(0);
      expect(lag).toBeLessThanOrEqual(1);
    }
    for (let i = 0; i < 500; i++) {
      const value = hash(i, i * 7, 3);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it("pins the easing to its ends", () => {
    expect(ease(-1)).toBe(0);
    expect(ease(0)).toBe(0);
    expect(ease(0.5)).toBeCloseTo(0.5);
    expect(ease(1)).toBe(1);
    expect(ease(2)).toBe(1);
  });

  it("writes a clip that measures from each edge of the screen", () => {
    expect(inset({ left: 10, top: 20, right: 110, bottom: 220 }, 400, 300, 30)).toBe(
      "inset(20px 290px 80px 10px round 30px)",
    );
  });

  it("turns a token hex into rgba", () => {
    expect(rgba("#8a5cff", 0.5)).toBe("rgba(138, 92, 255, 0.5)");
  });
});
