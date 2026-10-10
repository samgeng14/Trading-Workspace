import { describe, expect, it } from "vitest";
import { annualizedVol, sharpe } from "./stats";
import { ETFS, HEDGE, history, INCEPTION, navAt, snapshot, START_NAV } from "./sim";

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
    expect(Math.min(...hist.book.map((d) => d.cash))).toBeGreaterThan(-0.02 * START_NAV);
    expect(last.holdings.size).toBe(ETFS.length + 1);
    const hedge = last.holdings.get(HEDGE.symbol)!;
    const hedgeNotional = hedge.qty * HEDGE.multiplier * hist.close.get(HEDGE.symbol)!.at(-1)!;
    expect(hedgeNotional / last.nav).toBeLessThan(-0.15);
    expect(hedgeNotional / last.nav).toBeGreaterThan(-0.25);
    console.log({
      nav: Math.round(last.nav),
      itd: (last.nav / START_NAV - 1).toFixed(4),
      vol: vol.toFixed(3),
      sharpe: sharpe(rets).value.toFixed(2),
      cash: Math.round(last.cash),
      trades: hist.book.reduce((n, d) => n + d.trades.length, 0),
    });
  });

  it("prices a live session along the intraday path", () => {
    const open = snapshot("2026-10-09", true, new Date("2026-10-09T13:30:00Z"));
    expect(open.price("SPY")).toBeCloseTo(open.prevClose("SPY"), 8);
    const close = snapshot("2026-10-09", true, new Date("2026-10-09T20:00:00Z"));
    expect(close.price("SPY")).toBeCloseTo(hist.close.get("SPY")!.at(-1)!, 8);
    const done = snapshot("2026-10-09", false, new Date("2026-10-10T12:00:00Z"));
    expect(navAt(done)).toBe(last.nav);
  });
});
