import { describe, expect, it } from "vitest";
import {
  assumption,
  gain,
  label,
  perLap,
  percentOf,
  signed,
  staleness,
} from "./format";

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

describe("assumption", () => {
  it("puts a pit loss in seconds and a degradation rate in milliseconds", () => {
    expect(assumption("pit_loss_millis", 23648.867)).toBe("23.65 s");
    expect(assumption("degradation_millis_per_lap", 48.87)).toBe("49 ms");
    expect(assumption("dirty_air_millis", 735.248)).toBe("735 ms");
  });

  it("reads a per-lap hazard per thousand and a probability as a percentage", () => {
    expect(assumption("safety_car_per_green_lap", 0.0113)).toBe("11.30‰");
    expect(assumption("pass_probability_at_parity", 0.1333)).toBe("13.3%");
  });

  it("leaves anything it has no unit for alone", () => {
    expect(assumption("laps", 70)).toBe("70");
    expect(assumption("race_day_sd_percent", 0.5921)).toBe("0.59");
  });
});

describe("gain", () => {
  it("always carries a sign, and a real minus rather than a hyphen", () => {
    expect(gain(0.0137)).toBe("+0.0137");
    expect(gain(-0.0184)).toBe("−0.0184");
    expect(gain(0)).toBe("+0.0000");
  });
});
