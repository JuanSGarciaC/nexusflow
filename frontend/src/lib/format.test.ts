import { describe, expect, it } from "vitest";

import { formatDuration, formatPercent, shortId } from "./format";

describe("formato", () => {
  it.each([
    [null, "—"],
    [250, "250 ms"],
    [1500, "1.50 s"],
    [45_000, "45.0 s"],
    [125_000, "2 min 5 s"],
  ])("duración %s → %s", (ms, expected) => {
    expect(formatDuration(ms)).toBe(expected);
  });

  it("porcentaje y id corto", () => {
    expect(formatPercent(0.6)).toBe("60.0 %");
    expect(formatPercent(null)).toBe("—");
    expect(shortId("0123456789abcdef")).toBe("01234567");
  });
});
