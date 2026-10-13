import { describe, expect, it } from "vitest";
import { MINUS } from "./format";
import { limitText } from "./labels";

describe("limitText", () => {
  it("prints shares of NAV to one decimal", () => {
    expect(limitText(1.199, "pct")).toBe("119.9%");
    expect(limitText(-0.047, "pct")).toBe(`${MINUS}4.7%`);
    expect(limitText(0.0006, "pct")).toBe("0.1%");
    expect(limitText(0, "pct")).toBe("0.0%");
    expect(limitText(0.24, "ratio")).toBe("0.24");
    expect(limitText(23, "count")).toBe("23");
  });

  it("never prints a small margin loan as 0.0%", () => {
    // $400, $4 and one cent of margin loan on $1M of NAV
    expect(limitText(-400 / 1e6, "pct")).toBe(`${MINUS}0.04%`);
    expect(limitText(-4 / 1e6, "pct")).toBe(`${MINUS}0.0004%`);
    expect(limitText(-0.01 / 1e6, "pct")).toBe(`${MINUS}0.000001%`);
    expect(limitText(0.0003, "pct")).toBe("0.03%");
  });

  it("prints float noise as zero", () => {
    expect(limitText(-2e-16, "pct")).toBe("0.0%");
  });
});
