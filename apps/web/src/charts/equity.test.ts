import { describe, expect, it } from "vitest";
import { equityLines } from "./equity";

// Three days with no deposits or withdrawals: growth of $100 is NAV rebased to 100.
const series = [
  { date: "2026-09-30", portfolio: 100, benchmark: 100, nav: 1_040_000 },
  { date: "2026-10-01", portfolio: 101, benchmark: 99.5, nav: 1_050_400 },
  { date: "2026-10-02", portfolio: 102.5, benchmark: 101.2, nav: 1_066_000 },
];

describe("equity lines", () => {
  it("growth mode is the API's growth of $100", () => {
    const g = equityLines(series, "growth");
    expect(g.portfolio).toEqual([100, 101, 102.5]);
    expect(g.benchmark).toEqual([100, 99.5, 101.2]);
    expect(g.base).toBe(100);
  });

  it("NAV mode is dollars, with the benchmark grown from the same starting NAV", () => {
    const n = equityLines(series, "nav");
    expect(n.portfolio).toEqual([1_040_000, 1_050_400, 1_066_000]);
    expect(n.base).toBe(1_040_000);
    expect(n.benchmark[0]).toBe(1_040_000);
    expect(n.benchmark[1]).toBeCloseTo(1_040_000 * 0.995, 6);
    expect(n.benchmark[2]).toBeCloseTo(1_040_000 * 1.012, 6);
    // Without cash flows both modes draw the same shape on different axes.
    n.portfolio.forEach((v, i) => expect((100 * v) / n.base).toBeCloseTo(series[i]!.portfolio, 9));
  });

  it("handles an empty series", () => {
    expect(equityLines([], "nav")).toEqual({ portfolio: [], benchmark: [], base: 100 });
  });
});
