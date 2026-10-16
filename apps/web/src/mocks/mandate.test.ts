import { describe, expect, it } from "vitest";
import { closeTime, etTime, OPEN, prevTradingDay } from "./calendar";
import { previewOf } from "./handlers";
import {
  apply,
  type Book,
  type BookOrder,
  type BookPosition,
  check,
  exposure,
  money,
  pct,
  type Policy,
  POLICY,
} from "./mandate";
import {
  ANCHOR_DATE,
  bookOf,
  HEDGE,
  history,
  type History,
  INCEPTION,
  INSTRUMENTS,
  intradayPath,
  navAt,
  SAMPLE_SECONDS,
  sessionSteps,
  snapshot,
  snapshotOf,
} from "./sim";

// The same cases as engine/tests/test_risk.cpp: the port must reach the engine's verdicts.
const etf = (symbol: string, quantity: number, price: number, exposure = 1): BookPosition => ({
  symbol,
  quantity,
  price,
  multiplier: 1,
  settlesInCash: true,
  exposure,
});
const buy = (symbol: string, quantity: number, price: number, commission = 0, exposure = 1): BookOrder => ({
  ...etf(symbol, quantity, price, exposure),
  commission,
});
const mandateOnly: Policy = { ...POLICY, maxSingleName: 1 };
const find = (r: ReturnType<typeof check>, rule: string) => r.breaches.find((b) => b.rule === rule);

describe("the no-leverage check (port of engine/src/risk.cpp)", () => {
  it("passes an unlevered buy", () => {
    const book: Book = {
      nav: 1_000_000,
      cash: 100_000,
      positions: [etf("SPY", 450, 500), etf("TLT", 2250, 100), etf("IWM", 900, 250), etf("QQQ", 450, 500)],
    };
    const r = check(book, [buy("GLD", 125, 400)]);
    expect(r.ok).toBe(true);
    expect(r.breaches).toEqual([]);
    expect(r.reduceOnly).toBe(false);
    expect(r.before.gross).toBeCloseTo(0.9, 12);
    expect(r.after.gross).toBeCloseTo(0.95, 12);
    expect(r.after.cash).toBeCloseTo(50_000, 6);
  });

  it("blocks buying past NAV on gross and cash, in the engine's words", () => {
    const r = check({ nav: 1_000_000, cash: 10_000, positions: [etf("SPY", 1980, 500)] }, [buy("TLT", 400, 100)], {
      ...mandateOnly,
    });
    expect(r.ok).toBe(false);
    expect(r.after.gross).toBeCloseTo(1.03, 12);
    expect(find(r, "gross")!.message).toBe("gross 103.0% of NAV after these orders, over the 100.0% limit by $30,000");
    expect(find(r, "cash")!.message).toBe("cash -$30,000 after these orders: a margin loan, short by $30,000");
  });

  it("lets a levered book sell down, and blocks a basket that buys anything on it", () => {
    const book: Book = { nav: 1_000_000, cash: -25_000, positions: [etf("SPY", 2050, 500)] };
    const down = check(book, [buy("SPY", -20, 500)], mandateOnly);
    expect(down.reduceOnly).toBe(true);
    expect(down.ok).toBe(true);
    expect(find(down, "gross")!.blocking).toBe(false);
    expect(find(down, "gross")!.message).toContain("these orders only reduce positions, so they may go");
    const mixed = check(book, [buy("SPY", -20, 500), buy("TLT", 50, 100)], mandateOnly);
    expect(mixed.reduceOnly).toBe(false);
    expect(mixed.ok).toBe(false);
  });

  it("counts futures at notional and moves no cash for them", () => {
    const mes: BookOrder = { ...buy("MESZ6", -5, 6650, 2.5), multiplier: 5, settlesInCash: false };
    const r = check({ nav: 1_000_000, cash: 1_000_000, positions: [] }, [mes]);
    expect(r.ok).toBe(true);
    expect(r.after.shortValue).toBeCloseTo(166_250, 6);
    expect(r.after.cash).toBeCloseTo(1_000_000 - 2.5, 9);
    const es: BookOrder = { ...buy("ESZ6", 4, 6650), multiplier: 50, settlesInCash: false };
    const big = check({ nav: 1_000_000, cash: 1_000_000, positions: [] }, [es], mandateOnly);
    expect(big.ok).toBe(false);
    expect(find(big, "gross")!.blocking).toBe(true);
    expect(find(big, "cash")).toBeUndefined();
  });

  it("counts a leveraged ETF at its leverage, and says so", () => {
    const book: Book = { nav: 1_000_000, cash: 50_000, positions: [etf("SPY", 1800, 500), etf("TQQQ", 500, 100, 3)] };
    const idle = check(book, [], mandateOnly);
    expect(idle.before.gross).toBeCloseTo(1.05, 12);
    expect(idle.ok).toBe(true); // nothing to send: reported, not blocking
    const r = check(book, [buy("TQQQ", 10, 100, 0, 3)], mandateOnly);
    expect(r.ok).toBe(false);
    expect(r.after.cash).toBeCloseTo(49_000, 6); // cash moves at market value, not exposure
    expect(find(r, "gross")!.message).toContain("(counting TQQQ 3x at that exposure)");
  });

  it("allows exactly 100% gross long/short and blocks one more share", () => {
    const book: Book = { nav: 1_000_000, cash: 500_000, positions: [etf("SPY", 1000, 500)] };
    const r = check(book, [buy("IWM", -2000, 250)], mandateOnly);
    expect(r.ok).toBe(true);
    expect(r.after.gross).toBeCloseTo(1, 12);
    expect(r.after.cash).toBeCloseTo(1_000_000, 6);
    expect(check(book, [buy("IWM", -2001, 250)], mandateOnly).ok).toBe(false);
  });

  it("caps a single name at 25%", () => {
    const book: Book = { nav: 1_000_000, cash: 700_000, positions: [etf("SPY", 600, 500)] };
    const r = check(book, [buy("SPY", 100, 500)]);
    expect(find(r, "single_name")!.message).toBe("SPY is 35.0% of NAV after these orders, over the 25.0% limit");
  });

  it("re-marks a held position at the fill price and charges commission", () => {
    const after = apply({ nav: 1_000_000, cash: 500_000, positions: [etf("SPY", 1000, 500)] }, [
      buy("SPY", 10, 510, 1),
    ]);
    expect(after.nav).toBeCloseTo(1_000_000 + 1000 * 10 - 1, 9);
    expect(after.cash).toBeCloseTo(500_000 - 5100 - 1, 9);
  });
});

describe("the port's messages, number for number", () => {
  // Verdicts and messages from the compiled engine (dv02_engine.risk.check) on these books.
  it("rounds an exact tie to even, as printf and Python's format do", () => {
    expect(pct(0.0625)).toBe("6.2%");
    expect(pct(0.0675)).toBe("6.8%");
    expect(pct(-0.0625)).toBe("-6.2%");
    expect(pct(0.9949)).toBe("99.5%");
    const name = check({ nav: 1_000_000, cash: 675_000, positions: [etf("SPY", 1300, 250)] }, [buy("SPY", 30, 250)]);
    expect(name.breaches.map((b) => b.message)).toEqual([
      "SPY is 33.2% of NAV after these orders, over the 25.0% limit",
    ]);
    const gross = check(
      { nav: 1_000_000, cash: 0, positions: [etf("SPY", 2000, 500)] },
      [buy("TLT", 125, 100)],
      mandateOnly,
    );
    expect(gross.breaches.map((b) => b.message)).toEqual([
      "gross 101.2% of NAV after these orders, over the 100.0% limit by $12,500",
      "cash -$12,500 after these orders: a margin loan, short by $12,500",
    ]);
  });

  it("prints gearing with %g and a NAV it can't use as C does", () => {
    const r = check(
      { nav: 1_000_000, cash: 0, positions: [etf("SPY", 2000, 500)] },
      [buy("XYZ", 300, 100, 0, 1 / 3)],
      mandateOnly,
    );
    expect(r.breaches[0]!.message).toBe(
      "gross 101.0% of NAV after these orders, over the 100.0% limit by $10,000 (counting XYZ 0.333333x at that exposure)",
    );
    expect(check({ nav: NaN, cash: 0, positions: [] }, []).breaches[0]!.message).toBe(
      "NAV is $nan, so leverage can't be measured",
    );
    expect(check({ nav: -Infinity, cash: 0, positions: [] }, []).breaches[0]!.message).toBe(
      "NAV is -$inf, so leverage can't be measured",
    );
    expect(money(1234567.5)).toBe("$1,234,568");
    expect(money(-0.4)).toBe("$0");
    expect(money(1e21)).toBe("$1,000,000,000,000,000,000,000");
  });
});

/** history(last) cut at `date`: what history(date) returns when both are on the same price scale. */
function through(hist: History, date: string): History {
  const n = hist.dates.indexOf(date) + 1;
  const book = hist.book.slice(0, hist.bookIndex.get(date)! + 1);
  return {
    dates: hist.dates.slice(0, n),
    close: new Map([...hist.close].map(([s, px]) => [s, px.slice(0, n)])),
    book,
    bookIndex: new Map(book.map((d, k) => [d.date, k])),
  };
}

// A demo_now before ANCHOR_DATE shows an unrescaled history (its own price
// levels, share counts and trim days); from it on, every history is a prefix of
// the one through 2027, the end of the demo's calendar. Between them they are
// every book the demo can show.
const SCALES: [string, string][] = [
  ["before the anchor date", prevTradingDay(ANCHOR_DATE)],
  ["from the anchor date through 2027", "2027-12-31"],
];

describe.each(SCALES)("the demo book under the mandate, %s", (_label, last) => {
  const hist = history(last);
  const start = hist.dates.indexOf(INCEPTION);
  const days = hist.book.map((d) => d.date);

  it("is what the demo serves on those days", () => {
    const served = days.filter((d) => d < ANCHOR_DATE === last < ANCHOR_DATE);
    for (const date of [served[1]!, served[Math.floor(served.length / 2)]!, served.at(-2)!]) {
      const own = history(date);
      const cut = through(hist, date);
      expect(cut.dates).toEqual(own.dates);
      for (const [s, px] of own.close) expect(cut.close.get(s), `${date} ${s}`).toEqual(px);
      expect(cut.book).toEqual(own.book);
      for (const [at, live] of [
        [etTime(date, "11:03"), true],
        [etTime(date, "17:00"), false],
      ] as const) {
        const a = snapshotOf(cut, live, at);
        const b = snapshot(date, live, at);
        expect(navAt(a)).toBe(navAt(b));
        expect(a.price("SPY")).toBe(b.price("SPY"));
        expect(previewOf(at, a)).toEqual(previewOf(at, b));
      }
    }
  });

  it("stays inside it at every close and every 15 s of every session, futures at notional", () => {
    let maxClose = 0;
    let maxLive = 0;
    let minCash = Infinity;
    let maxName = 0;
    for (let b = 0; b < hist.book.length; b++) {
      const day = hist.book[b]!;
      const px = (s: string) => hist.close.get(s)![start + b]!;
      const r = check(bookOf(day.nav, day.cash, day.holdings, px), []);
      expect(r.breaches, day.date).toEqual([]);
      // every holding at |quantity x multiplier x price|: the hedge at its full notional
      const notional = [...day.holdings].reduce(
        (a, [s, h]) => a + Math.abs(h.qty * INSTRUMENTS.get(s)!.multiplier * px(s)),
        0,
      );
      expect(r.before.gross, day.date).toBeCloseTo(notional / day.nav, 12);
      expect(r.before.shortValue, day.date).toBeCloseTo(
        -day.holdings.get(HEDGE.symbol)!.qty * HEDGE.multiplier * px(HEDGE.symbol),
        6,
      );
      maxClose = Math.max(maxClose, r.before.gross);
      minCash = Math.min(minCash, r.before.cashRatio);
      if (b === 0) continue; // inception day: no book before the close

      // live: the previous close's book on the session's price path, at every
      // 15 s step. Prices run straight (in log) between the steps, so the steps
      // bound every second in between.
      const prev = hist.book[b - 1]!;
      const held = [...prev.holdings].map(([s, h]) => {
        const inst = INSTRUMENTS.get(s)!;
        const p0 = hist.close.get(s)![start + b - 1]!;
        return {
          units: h.qty * inst.multiplier,
          p0,
          future: inst.secType === "FUT",
          path: intradayPath(s, day.date, Math.log(px(s) / p0)),
        };
      });
      const n = sessionSteps(day.date);
      const grossAt = new Float64Array(n + 1);
      let tightest = 0;
      for (let k = 0; k <= n; k++) {
        let nav = prev.cash;
        let gross = 0;
        let name = 0;
        for (const p of held) {
          const price = p.p0 * Math.exp(p.path[k]!);
          nav += p.future ? p.units * (price - p.p0) : p.units * price;
          gross += Math.abs(p.units * price);
          name = Math.max(name, Math.abs(p.units * price));
        }
        grossAt[k] = gross / nav;
        if (grossAt[k]! > grossAt[tightest]!) tightest = k;
        minCash = Math.min(minCash, prev.cash / nav);
        maxName = Math.max(maxName, name / nav);
      }
      maxLive = Math.max(maxLive, grossAt[tightest]!);
      // the same numbers as the demo computes them, at the day's tightest step and a few more
      const cut = through(hist, day.date);
      const open = etTime(day.date, OPEN).getTime();
      for (const k of [0, tightest, n >> 1, n]) {
        const snap = snapshotOf(cut, true, new Date(open + k * SAMPLE_SECONDS * 1000));
        const e = exposure(bookOf(navAt(snap), snap.record.cash, snap.record.holdings, snap.price));
        expect(e.gross, `${day.date} step ${k}`).toBeCloseTo(grossAt[k]!, 12);
      }
    }
    expect(maxClose).toBeLessThanOrEqual(1);
    expect(maxLive).toBeLessThanOrEqual(1);
    expect(minCash).toBeGreaterThanOrEqual(0);
    expect(maxName).toBeLessThanOrEqual(POLICY.maxSingleName);
    expect(maxClose).toBeGreaterThan(0.9); // invested, not idling in cash
  }, 120_000);

  it("passes the preview's no-leverage check through every session and after every close", () => {
    for (const date of days.slice(1)) {
      const cut = through(hist, date);
      const open = etTime(date, OPEN).getTime();
      const close = etTime(date, closeTime(date)).getTime();
      for (const [t, live] of [
        [open, true],
        [(open + close) / 2, true],
        [close - 1000, true],
        [close + 3_600_000, false],
      ] as const) {
        const at = new Date(t);
        const leverage = previewOf(at, snapshotOf(cut, live, at)).checks.find((c) => c.id === "leverage")!;
        expect(leverage.status, `${at.toISOString()}: ${leverage.detail}`).toBe("pass");
      }
    }
  }, 120_000);
});

describe("the demo preview's no-leverage check", () => {
  const at = new Date("2026-10-09T15:00:00Z"); // Friday 11:00 ET, session open
  const base = snapshot("2026-10-09", true, at);
  /** The demo book at `at` with extra holdings; `cash` pays for them like a margin loan would. */
  const bookWith = (extra: [string, number][], cash: number) => ({
    ...base,
    record: {
      ...base.record,
      cash: base.record.cash + cash,
      holdings: new Map([
        ...base.record.holdings,
        ...extra.map(
          ([s, qty]) => [s, { qty: qty + (base.record.holdings.get(s)?.qty ?? 0), avgCost: base.price(s) }] as const,
        ),
      ]),
    },
  });

  it("passes the normal rebalance, so the demo can submit it", () => {
    const p = previewOf(at, base);
    const leverage = p.checks.find((c) => c.id === "leverage")!;
    expect(leverage.status).toBe("pass");
    expect(leverage.detail).not.toMatch(/demo/i);
    expect(p.can_submit).toBe(true);
  });

  it("fails a deliberately levered book, in the engine's words, and blocks the submit", () => {
    // twenty more short MES contracts: about 60% of NAV of extra short notional, no cash moved
    const levered = bookWith([[HEDGE.symbol, -20]], 0);
    const p = previewOf(at, levered);
    const leverage = p.checks.find((c) => c.id === "leverage")!;
    expect(leverage.status).toBe("fail");
    expect(leverage.detail).toMatch(
      /^gross 1\d\d\.\d% of NAV after these orders, over the 100\.0% limit by \$[\d,]+; /,
    );
    expect(leverage.detail).toMatch(/; MESZ6 is \d\d\.\d% of NAV after these orders, over the 25\.0% limit$/);
    expect(p.can_submit).toBe(false);
  });

  it("sells holdings outside the model at this rebalance, which brings a margin loan back inside", () => {
    // IWM, a benchmark the model doesn't hold, bought with borrowed money
    const px = base.price("IWM");
    const shares = 1500;
    const book = bookWith([["IWM", shares]], -shares * px);
    expect(book.record.cash).toBeLessThan(0);
    const p = previewOf(at, book);
    const row = p.rows.find((r) => r.symbol === "IWM")!;
    expect(row).toMatchObject({ target_weight: 0, target_qty: 0, current_qty: shares, trade_qty: -shares });
    expect(p.checks.find((c) => c.id === "untargeted")).toMatchObject({
      status: "warn",
      detail: "IWM: not in the model, so this rebalance sells them",
    });
    const leverage = p.checks.find((c) => c.id === "leverage")!;
    expect(leverage.detail).toMatch(/^gross 1\d\d\.\d% → \d\d\.\d% of NAV/);
    expect(leverage.status).toBe("pass");
  });
});
