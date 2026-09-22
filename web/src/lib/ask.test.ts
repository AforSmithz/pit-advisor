import { describe, expect, it } from "vitest";

import { payloadHash } from "./ask";

describe("payloadHash", () => {
  it("is the hex sha-256 cloudfront signs with", async () => {
    // printf %s '{"question":"hi"}' | shasum -a 256
    expect(await payloadHash('{"question":"hi"}')).toBe(
      "b94e79e3f4050e82ba8fc4f4fa0247334a05b5ef31e9113a98ebafdb08d0c833",
    );
  });

  it("hashes the empty body too", async () => {
    expect(await payloadHash("")).toBe(
      "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    );
  });
});
