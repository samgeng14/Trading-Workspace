import { describe, expect, it } from "vitest";
import {
  annualizedReturn,
  compound,
  drawdowns,
  hitRate,
  maxDrawdown,
  relative,
  returnsOf,
  sharpe,
  sortino,
  stdev,
} from "./stats";

describe("stats", () => {
  it("compounds and annualizes", () => {
    expect(compound([0.1, -0.1])).toBeCloseTo(-0.01, 12);
    expect(annualizedReturn(new Array(252).fill(0.001))).toBeCloseTo(1.001 ** 252 - 1, 12);
  });

  it("uses the sample standard deviation", () => {
    expect(stdev([1, 2, 3, 4])).toBeCloseTo(Math.sqrt(5 / 3), 12);
  });

  it("computes Sharpe with a widening interval for short records", () => {
    const rets = Array.from({ length: 252 }, (_, i) => (i % 2 === 0 ? 0.011 : -0.009));
    const s = sharpe(rets);
    const expected = (0.001 / stdev(rets)) * Math.sqrt(252);
    expect(s.value).toBeCloseTo(expected, 10);
    expect(s.years).toBe(1);
    const se = Math.sqrt((1 + expected ** 2 / 2) / 1);
    expect(s.ci_high - s.ci_low).toBeCloseTo(2 * 1.96 * se, 10);
    const longer = sharpe([...rets, ...rets, ...rets, ...rets]);
    expect(longer.ci_high - longer.ci_low).toBeLessThan(s.ci_high - s.ci_low);
  });

  it("measures drawdowns from the running peak", () => {
    expect(drawdowns([100, 110, 99, 120])).toEqual([0, 0, 99 / 110 - 1, 0]);
    expect(maxDrawdown([100, 80, 120, 60])).toBeCloseTo(-0.5, 12);
  });

  it("recovers beta and zero tracking error for a scaled benchmark", () => {
    const bench = [0.01, -0.02, 0.015, 0.003, -0.007];
    const port = bench.map((r) => 2 * r);
    const rel = relative(port, bench);
    expect(rel.beta).toBeCloseTo(2, 12);
    expect(rel.alpha_ann).toBeCloseTo(0, 12);
    const same = relative(bench, bench);
    expect(same.tracking_error).toBe(0);
    expect(same.information_ratio).toBe(0);
  });

  it("handles sortino, hit rate and returns", () => {
    expect(hitRate([0.01, -0.01, 0.02, 0])).toBe(0.5);
    expect(sortino([0.01, 0.02])).toBe(0);
    const rets = returnsOf([100, 110, 99]);
    expect(rets).toHaveLength(2);
    expect(rets[0]).toBeCloseTo(0.1, 12);
    expect(rets[1]).toBeCloseTo(99 / 110 - 1, 12);
  });
});
