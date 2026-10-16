import { describe, expect, it } from "vitest";
import { annualizedVol, sharpe } from "./stats";
import {
  ETFS,
  HEDGE,
  HEDGE_RATIO,
  history,
  INCEPTION,
  momentumTargets,
  navAt,
  SLEEVE_WEIGHT,
  snapshot,
  START_NAV,
} from "./sim";

describe("demo simulation", () => {
  const hist = history("2026-10-09");
  const last = hist.book[hist.book.length - 1]!;

  it("starts at inception with $1M and is deterministic", () => {
    expect(hist.book[0]!.date).toBe(INCEPTION);
    expect(hist.book[0]!.nav).toBeGreaterThan(START_NAV * 0.995);
    const again = history("2026-10-08");
    expect(again.book.at(-1)!.nav).toBe(hist.book.at(-2)!.nav);
  });

  it("keeps the book sane", () => {
    const rets = hist.book.slice(1).map((d) => d.ret);
    const vol = annualizedVol(rets);
    expect(vol).toBeGreaterThan(0.04);
    expect(vol).toBeLessThan(0.3);
    // no margin loan, ever: the hedge's losses come out of the cash the sleeve leaves
    expect(Math.min(...hist.book.map((d) => d.cash))).toBeGreaterThanOrEqual(0);
    expect(last.holdings.size).toBe(ETFS.length + 1);
    const hedge = last.holdings.get(HEDGE.symbol)!;
    const hedgePx = hist.close.get(HEDGE.symbol)!.at(-1)!;
    const hedgeNotional = hedge.qty * HEDGE.multiplier * hedgePx;
    // the hedge sits within two contracts of its target, as the simulation re-hedges
    const contract = HEDGE.multiplier * hedgePx;
    expect(Math.abs(hedgeNotional + HEDGE_RATIO * last.nav)).toBeLessThanOrEqual(2 * contract);
    expect(hedgeNotional).toBeLessThan(0);
    console.log({
      nav: Math.round(last.nav),
      itd: (last.nav / START_NAV - 1).toFixed(4),
      vol: vol.toFixed(3),
      sharpe: sharpe(rets).value.toFixed(2),
      cash: Math.round(last.cash),
      trades: hist.book.reduce((n, d) => n + d.trades.length, 0),
    });
  });

  it("holds the 21-ETF model without TQQQ, sized to leave room for the hedge", () => {
    const symbols = ETFS.map((e) => e.symbol);
    expect(symbols).toHaveLength(21);
    expect(symbols).not.toContain("TQQQ");
    expect(symbols).toContain("QQQ");
    expect(ETFS.every((e) => !e.leveraged)).toBe(true);
    const targets = momentumTargets(hist.close, hist.dates.length - 1);
    const sum = [...targets.values()].reduce((a, w) => a + w, 0);
    expect(sum).toBeCloseTo(SLEEVE_WEIGHT, 12);
    expect(SLEEVE_WEIGHT + HEDGE_RATIO).toBeLessThan(1);
  });

  it("prices a live session along the intraday path", () => {
    const open = snapshot("2026-10-09", true, new Date("2026-10-09T13:30:00Z"));
    expect(open.price("SPY")).toBeCloseTo(open.prevClose("SPY"), 8);
    const close = snapshot("2026-10-09", true, new Date("2026-10-09T20:00:00Z"));
    expect(close.price("SPY")).toBeCloseTo(hist.close.get("SPY")!.at(-1)!, 8);
    const done = snapshot("2026-10-09", false, new Date("2026-10-10T12:00:00Z"));
    expect(navAt(done)).toBe(last.nav);
    // prices step every 15 s and run straight between steps, to the second
    const at = (iso: string) => snapshot("2026-10-09", true, new Date(iso)).price("SPY");
    const [a, b, mid] = [at("2026-10-09T15:00:00Z"), at("2026-10-09T15:00:15Z"), at("2026-10-09T15:00:07.900Z")];
    expect(b).not.toBe(a);
    expect(Math.min(a, b) <= mid && mid <= Math.max(a, b)).toBe(true);
    expect(mid).toBe(at("2026-10-09T15:00:07Z"));
  });
});
