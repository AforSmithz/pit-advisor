import { describe, expect, it } from "vitest";
import { label, perLap, percentOf, signed, staleness } from "./format";

describe("signed", () => {
  it("uses a real minus sign and keeps the sign visible", () => {
    expect(signed(-0.65)).toBe("−0.65");
    expect(signed(0.25)).toBe("+0.25");
  });

  it("does not sign a zero", () => {
    expect(signed(0)).toBe("0.00");
    expect(signed(-0.0004)).toBe("0.00");
  });
});

describe("perLap", () => {
  it("renders a hazard as per mille", () => {
    expect(perLap(0.0022143)).toBe("2.21‰");
  });
});

describe("label", () => {
  it("engraves an id without inventing a name", () => {
    expect(label("yas_marina")).toBe("YAS MARINA");
  });
});

describe("percentOf", () => {
  it("rounds a weight to whole percent", () => {
    expect(percentOf(1)).toBe("100%");
    expect(percentOf(0.334)).toBe("33%");
  });
});

describe("staleness", () => {
  const now = new Date("2026-08-30T12:00:00Z");
  it("counts hours then days", () => {
    expect(staleness("2026-08-30T07:00:00Z", now)).toBe("5h ago");
    expect(staleness("2026-08-25T12:00:00Z", now)).toBe("5d ago");
  });
});
