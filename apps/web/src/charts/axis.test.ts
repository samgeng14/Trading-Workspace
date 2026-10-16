import { scaleLinear } from "d3-scale";
import { describe, expect, it } from "vitest";
import { MINUS, num } from "../lib/format";
import {
  dollarTicks,
  fitXTicks,
  nearestIndex,
  sessionMinutes,
  tickDecimals,
  tickTextWidth,
  xAnchor,
  Y_LABEL_GAP,
  yGutter,
  yScale,
} from "./axis";

const OPEN = "2026-10-09T13:30:00Z"; // 09:30 ET
const CLOSE = "2026-10-09T20:00:00Z"; // 16:00 ET

describe("intraday x from timestamps", () => {
  it("places each point by its own time, whatever the spacing or start", () => {
    // The API started at 11:07:23 ET and samples every 15 s; while live it adds the P&L now (11:08:30) last.
    const start = Date.parse("2026-10-09T15:07:23Z");
    const points = Array.from({ length: 5 }, (_, k) => ({ t: new Date(start + k * 15_000).toISOString(), pnl: k }));
    points.push({ t: "2026-10-09T15:08:30Z", pnl: 5 });
    const out = sessionMinutes(points, OPEN, CLOSE);
    expect(out.map((p) => p.minute)).toEqual([
      97 + 23 / 60,
      97 + 38 / 60,
      97 + 53 / 60,
      98 + 8 / 60,
      98 + 23 / 60,
      98 + 30 / 60,
    ]);
    // Not the index: the first point is 97 minutes in, not at the open, and "now" is 7 s after the last sample.
    const x = scaleLinear().domain([0, 390]).range([0, 780]);
    expect(x(out[0]!.minute)).toBeCloseTo(194.77, 2);
    expect(x(out[5]!.minute) - x(out[4]!.minute)).toBeCloseTo((7 / 60) * 2, 9);
  });

  it("keeps uneven gaps and sorts by time", () => {
    const out = sessionMinutes(
      [
        { t: "2026-10-09T14:30:00Z", pnl: 2 },
        { t: "2026-10-09T13:30:00Z", pnl: 1 },
        { t: "2026-10-09T19:59:45Z", pnl: 3 },
      ],
      OPEN,
      CLOSE,
    );
    expect(out.map((p) => [p.minute, p.pnl])).toEqual([
      [0, 1],
      [60, 2],
      [389.75, 3],
    ]);
  });

  it("drops points outside the session, keeping the open and close themselves", () => {
    const out = sessionMinutes(
      [
        { t: "2026-10-09T13:29:59Z", pnl: 0 },
        { t: OPEN, pnl: 1 },
        { t: CLOSE, pnl: 2 },
        { t: "2026-10-09T20:00:01Z", pnl: 3 },
      ],
      OPEN,
      CLOSE,
    );
    expect(out.map((p) => p.pnl)).toEqual([1, 2]);
  });

  it("finds the point nearest the pointer", () => {
    const xs = [0, 0.25, 0.5, 10, 10.25];
    expect(nearestIndex(xs, 0.3)).toBe(1);
    expect(nearestIndex(xs, 6)).toBe(3);
    expect(nearestIndex(xs, -5)).toBe(0);
    expect(nearestIndex(xs, 400)).toBe(4);
    expect(nearestIndex([], 3)).toBe(-1);
  });
});

describe("y axis", () => {
  it("covers the data and the baseline with nice ends", () => {
    const y = yScale([96.2, 113.4, 104], 200, { baseline: 100 });
    const [lo, hi] = y.domain() as [number, number];
    expect(lo).toBeLessThanOrEqual(96.2);
    expect(hi).toBeGreaterThanOrEqual(113.4);
    expect(y.ticks(5)).toContain(100);
    expect(y(lo)).toBe(200);
    expect(y(hi)).toBe(0);
  });

  it("caps at yMax and gives a flat series a readable range", () => {
    expect(yScale([-0.05, -0.01, 0], 100, { baseline: 0, yMax: 0 }).domain()[1]).toBe(0);
    const [lo, hi] = yScale([1_000_000, 1_000_000], 100).domain() as [number, number];
    expect(hi - lo).toBeGreaterThan(0);
  });

  it("gives the labels a gutter of their own, clear of the first point at any width", () => {
    for (const values of [
      [100, 92.4, 115.1],
      [1_000_000, 1_150_000, 880_000],
      [-0.12, 0],
    ]) {
      const y = yScale(values, 240);
      const ticks = y.ticks(5);
      const fmt = values[0]! > 1000 ? dollarTicks(ticks) : (v: number) => String(v);
      const labels = ticks.map(fmt);
      const gutter = yGutter(labels);
      for (const label of labels) {
        // Labels end Y_LABEL_GAP left of the plot; at 0.6em of 10.5px a glyph is 6.3px.
        expect(label.length * 6.3).toBeLessThanOrEqual(tickTextWidth(label));
        expect(tickTextWidth(label)).toBeLessThanOrEqual(gutter - Y_LABEL_GAP);
      }
      // The first point sits at the plot's left edge; its hover dot (r 4 + 1px of ring) stays clear.
      expect(Y_LABEL_GAP).toBeGreaterThan(5);
    }
  });
});

describe("x tick labels", () => {
  const months = ["Oct ’25", "Dec", "Feb", "Apr", "Jun", "Aug", "Oct"].map((text, i) => ({ at: i * 40, text }));
  const box = (t: { at: number; text: string }, x: (v: number) => number, innerW: number) => {
    const w = tickTextWidth(t.text);
    const a = xAnchor(x(t.at), innerW);
    const left = a === "start" ? x(t.at) : a === "end" ? x(t.at) - w : x(t.at) - w / 2;
    return [left, left + w] as const;
  };

  it("keeps every tick when there is room", () => {
    const x = scaleLinear().domain([0, 245]).range([0, 1000]);
    expect(fitXTicks(months, x, 1000)).toEqual(months);
  });

  for (const innerW of [140, 180, 240, 320]) {
    it(`thins to an even stride that never overlaps at ${innerW}px`, () => {
      const x = scaleLinear().domain([0, 245]).range([0, innerW]);
      const kept = fitXTicks(months, x, innerW);
      expect(kept[0]).toEqual(months[0]);
      expect(kept.length).toBeGreaterThan(1);
      const stride = months.indexOf(kept[1]!);
      kept.forEach((t, k) => expect(months.indexOf(t)).toBe(k * stride));
      for (let k = 1; k < kept.length; k++) {
        expect(box(kept[k]!, x, innerW)[0]).toBeGreaterThan(box(kept[k - 1]!, x, innerW)[1]);
      }
    });
  }
});

describe("dollar ticks", () => {
  it("share one unit and enough decimals to tell neighbours apart", () => {
    const f = dollarTicks([950_000, 1_000_000, 1_050_000, 1_100_000]);
    expect([950_000, 1_000_000, 1_050_000, 1_100_000].map(f)).toEqual(["$0.95M", "$1.00M", "$1.05M", "$1.10M"]);
    const fine = dollarTicks([1_000_000, 1_005_000, 1_010_000]);
    expect([1_000_000, 1_005_000, 1_010_000].map(fine)).toEqual(["$1.000M", "$1.005M", "$1.010M"]);
    expect([1_000_000, 2_000_000].map(dollarTicks([1_000_000, 2_000_000]))).toEqual(["$1M", "$2M"]);
    const pnl = dollarTicks([-5000, 0, 5000]);
    expect([-5000, 0, 5000].map(pnl)).toEqual([`${MINUS}$5K`, "$0", "$5K"]);
  });
});

describe("tick labels on a quiet window", () => {
  const labels = (
    values: number[],
    baseline: number,
    fmt: (ticks: number[]) => (v: number) => string,
    yMax?: number,
  ) => {
    const ticks = yScale(values, 240, { baseline, yMax }).ticks(5);
    return ticks.map(fmt(ticks));
  };
  const fixed = (ticks: number[]) => (v: number) => num(v, tickDecimals(ticks));

  it("never prints the same label twice", () => {
    for (const [values, baseline, fmt, yMax] of [
      [[1_000_000, 1_000_800], 1_000_000, dollarTicks], // a new account's first two days
      [[1_000_000, 1_000_001], 1_000_000, dollarTicks], // a dollar of interest on cash
      [[480_000, 480_350], 480_000, dollarTicks],
      [[100, 101.2, 99.6], 100, fixed], // growth of $100 over a quiet month
      [[100, 100.08], 100, fixed],
      [[0, -2.6, -1], 0, fixed, 0], // drawdown in percent
      [[0, -0.4], 0, fixed, 0],
    ] as const) {
      const out = labels([...values], baseline, fmt, yMax);
      expect(new Set(out).size, out.join(" ")).toBe(out.length);
    }
  });

  it("keeps the compact form while it can, and whole dollars below $1,000 steps", () => {
    expect(labels([1_000_000, 1_000_800], 1_000_000, dollarTicks)).toContain("$1,000,200");
    expect(labels([1_000_000, 1_004_000], 1_000_000, dollarTicks)).toContain("$1.002M");
    expect(labels([100, 112], 100, fixed)).toContain("105");
    expect(labels([0, -0.4], 0, fixed, 0)).toEqual(["−0.5", "−0.4", "−0.3", "−0.2", "−0.1", "0.0"]);
  });
});
