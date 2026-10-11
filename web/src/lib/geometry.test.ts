import { describe, expect, it } from "vitest";
import { along, arc, heat, polar, smoothPath } from "./geometry";

describe("geometry", () => {
  it("puts zero degrees at twelve o'clock", () => {
    const [x, y] = polar(100, 100, 50, 0);
    expect(x).toBeCloseTo(100);
    expect(y).toBeCloseTo(50);
  });

  it("flags the large arc only past half a turn", () => {
    expect(arc(0, 0, 10, 0, 90)).toContain(" 0 0 1 ");
    expect(arc(0, 0, 10, 0, 270)).toContain(" 0 1 1 ");
  });

  it("clamps a value to its axis", () => {
    expect(along(5, 0, 10)).toBe(0.5);
    expect(along(-3, 0, 10)).toBe(0);
    expect(along(30, 0, 10)).toBe(1);
    expect(along(1, 1, 1)).toBe(0.5);
  });

  it("runs the heat ramp from violet-black to yellow", () => {
    expect(heat(0)).toBe("rgb(29,21,48)");
    expect(heat(1)).toBe("rgb(255,210,90)");
    expect(heat(2)).toBe("rgb(255,210,90)");
  });

  it("draws one curve segment per gap between points", () => {
    const path = smoothPath([
      [0, 0],
      [10, 5],
      [20, 0],
    ]);
    expect(path.startsWith("M0.0 0.0")).toBe(true);
    expect(path.match(/C/g)).toHaveLength(2);
    expect(smoothPath([])).toBe("");
  });
});
