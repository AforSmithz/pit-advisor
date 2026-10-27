import { describe, expect, it } from "vitest";
import { bounds, clip, dissolve, flat, grown, hash, pulled, seams } from "./mosaic";

const left = { left: 0, top: 0, right: 600, bottom: 400 };
const right = { left: 616, top: 0, right: 1000, bottom: 900 };
const under = { left: 0, top: 416, right: 600, bottom: 900 };

describe("mosaic", () => {
  it("keeps the dissolve and the hash inside the unit interval", () => {
    for (const at of dissolve(80, 50, 0.42)) {
      expect(at).toBeGreaterThanOrEqual(0);
      expect(at).toBeLessThanOrEqual(1);
    }
    for (let i = 0; i < 500; i++) {
      const value = hash(i, i * 7, 3);
      expect(value).toBeGreaterThanOrEqual(0);
      expect(value).toBeLessThan(1);
    }
  });

  it("lets the left of the screen go before the right", () => {
    const cols = 80;
    const rows = 50;
    const at = dissolve(cols, rows, 0.42);
    const mean = (c: number) => {
      let sum = 0;
      for (let r = 0; r < rows; r++) sum += at[r * cols + c] ?? 0;
      return sum / rows;
    };
    expect(mean(0)).toBeLessThan(mean(40));
    expect(mean(40)).toBeLessThan(mean(79));
  });

  it("tiles the slab once every card takes its share of the gaps", () => {
    const slab = bounds([left, right, under]);
    expect(slab).toEqual({ left: 0, top: 0, right: 1000, bottom: 900 });
    const tiles = [left, right, under].map((box) => grown(box, 8, slab));
    expect(tiles[0]).toEqual({ left: 0, top: 0, right: 608, bottom: 408 });
    expect(tiles[1]).toEqual({ left: 608, top: 0, right: 1000, bottom: 900 });
    expect(tiles[2]).toEqual({ left: 0, top: 408, right: 608, bottom: 900 });
  });

  it("never clips a box inside out", () => {
    const out = clip({ left: 900, top: 900, right: 1200, bottom: 1300 }, left);
    expect(out.right).toBeGreaterThanOrEqual(out.left);
    expect(out.bottom).toBeGreaterThanOrEqual(out.top);
  });

  it("pulls a card smaller and away from the centre", () => {
    const out = pulled(left, { x: 500, y: 450 });
    expect(out.right - out.left).toBeLessThan(600);
    expect((out.left + out.right) / 2).toBeLessThan(300);
    expect((out.top + out.bottom) / 2).toBeLessThan(200);
  });

  it("finds the cards that face each other across a gap", () => {
    expect(seams([left, right, under])).toEqual([
      { a: 0, b: 1, axis: "x" },
      { a: 0, b: 2, axis: "y" },
      { a: 2, b: 1, axis: "x" },
    ]);
  });

  it("flattens a shadow list without touching its colours", () => {
    expect(flat("rgba(0, 0, 0, 0.8) 10px 12px 28px 0px, rgba(255, 255, 255, 0.028) -7px -7px 16px 0px inset")).toBe(
      "rgba(0, 0, 0, 0.8) 0px 0px 0px 0px, rgba(255, 255, 255, 0.028) 0px 0px 0px 0px inset",
    );
    expect(flat("none")).toBe("none");
  });
});
