// Deterministic simulation of the $1M paper book for demo mode.
//
// Everything is a pure function of the date: daily returns come from a
// small factor model seeded by (symbol, date), so reloading the page or
// running the e2e suite always shows the same history. The book mirrors the
// current live setup: the 22-ETF universe rebalanced monthly with a
// momentum tilt (sleeve "ETF Momentum"), plus a short micro E-mini S&P
// position standing in for the central risk book's beta hedge.

import {
  addDays,
  closeTime,
  etTime,
  isTradingDay,
  nextTradingDay,
  OPEN,
  prevTradingDay,
  tradingDays,
} from "./calendar";

export const INCEPTION = "2025-10-15";
export const START_NAV = 1_000_000;
const HISTORY_START = "2024-09-03"; // momentum lookback before inception
const ANCHOR_DATE = "2026-05-06"; // simulated paths pass through the real closes on this date
const CASH_YIELD = 0.042;
const COMMISSION_PER_SHARE = 0.005;
const SLIPPAGE = 0.0002;
const CASH_BUFFER = 0.005;
const HEDGE_RATIO = 0.2; // short S&P notional as a fraction of NAV
const DRIFT_BAND = 0.03;
const MOMENTUM_LOOKBACK = 126;

export type AssetClass = "equity" | "fixed_income" | "commodity" | "crypto" | "volatility" | "cash" | "index_future";

export interface Instrument {
  symbol: string;
  name: string;
  assetClass: AssetClass;
  secType: "ETF" | "FUT";
  multiplier: number;
  anchor: number; // price on ANCHOR_DATE
  vol: number; // annualized total vol
  drift: number; // annualized drift
  loadings: Partial<Record<Factor, number>>;
  /** Daily-reset leveraged product: return = leverage x underlying - fee. */
  leveraged?: { underlying: string; leverage: number };
}

type Factor = "mkt" | "rates" | "cmdty" | "crypto";
const FACTOR_SD: Record<Factor, number> = { mkt: 0.0098, rates: 0.0055, cmdty: 0.0125, crypto: 0.03 };

// Anchor prices are the last real closes in the repo's price master, used
// only to put the demo on realistic scales.
export const ETFS: Instrument[] = [
  etf("SPY", "SPDR S&P 500", "equity", 733.83, 0.16, 0.09, { mkt: 1 }),
  etf("QQQ", "Invesco QQQ (Nasdaq-100)", "equity", 695.77, 0.21, 0.11, { mkt: 1.2 }),
  {
    ...etf("TQQQ", "ProShares UltraPro QQQ", "equity", 71.57, 0.63, -0.0095, { mkt: 3.6 }),
    leveraged: { underlying: "QQQ", leverage: 3 },
  },
  etf("VEA", "Vanguard Developed Markets", "equity", 70.87, 0.15, 0.07, { mkt: 0.85 }),
  etf("VWO", "Vanguard Emerging Markets", "equity", 60.75, 0.18, 0.06, { mkt: 0.8 }),
  etf("INDA", "iShares MSCI India", "equity", 50.03, 0.17, 0.05, { mkt: 0.6 }),
  etf("VFH", "Vanguard Financials", "equity", 127.59, 0.2, 0.08, { mkt: 1.1 }),
  etf("VHT", "Vanguard Health Care", "equity", 273.1, 0.16, 0.05, { mkt: 0.7 }),
  etf("VIS", "Vanguard Industrials", "equity", 345.38, 0.18, 0.09, { mkt: 1.0 }),
  etf("VNQ", "Vanguard Real Estate", "equity", 97.09, 0.2, 0.04, { mkt: 0.9, rates: 0.8 }),
  etf("VPU", "Vanguard Utilities", "equity", 198.05, 0.15, 0.06, { mkt: 0.5, rates: 0.5 }),
  etf("TLT", "iShares 20+ Year Treasury", "fixed_income", 86.08, 0.145, 0.03, { rates: 1.6 }),
  etf("IEI", "iShares 3-7 Year Treasury", "fixed_income", 118.06, 0.05, 0.035, { rates: 0.55 }),
  etf("SHY", "iShares 1-3 Year Treasury", "fixed_income", 82.3, 0.018, 0.04, { rates: 0.18 }),
  etf("BIL", "SPDR 1-3 Month T-Bill", "cash", 91.42, 0.003, 0.043, {}),
  etf("GLD", "SPDR Gold Shares", "commodity", 430.96, 0.16, 0.1, { mkt: 0.1, cmdty: 0.3 }),
  etf("SLV", "iShares Silver Trust", "commodity", 70.13, 0.3, 0.08, { mkt: 0.3, cmdty: 0.5 }),
  etf("USO", "United States Oil Fund", "commodity", 133.95, 0.34, 0.02, { mkt: 0.3, cmdty: 1.0 }),
  etf("UNG", "United States Natural Gas", "commodity", 10.45, 0.5, -0.15, { cmdty: 0.4 }),
  etf("IBIT", "iShares Bitcoin Trust", "crypto", 46.19, 0.5, 0.15, { mkt: 0.5, crypto: 1.0 }),
  etf("ETHA", "iShares Ethereum Trust", "crypto", 17.72, 0.72, 0.1, { mkt: 0.6, crypto: 1.3 }),
  etf("VIXY", "ProShares VIX Short-Term Futures", "volatility", 27.09, 0.75, -0.45, { mkt: -3.5 }),
];

export const HEDGE: Instrument = {
  symbol: "MESZ6",
  name: "Micro E-mini S&P 500 Dec '26",
  assetClass: "index_future",
  secType: "FUT",
  multiplier: 5,
  anchor: 7340,
  vol: 0.16,
  drift: 0.09,
  loadings: { mkt: 1 },
};

// Benchmarks / reference markets that are not held.
const EXTRA: Instrument[] = [
  etf("IWM", "iShares Russell 2000", "equity", 245, 0.22, 0.08, { mkt: 1.2 }),
  etf("ACWI", "iShares MSCI ACWI", "equity", 128, 0.15, 0.08, { mkt: 0.9 }),
  etf("AGG", "iShares Core US Aggregate Bond", "fixed_income", 99, 0.055, 0.04, { rates: 0.45 }),
];

function etf(
  symbol: string,
  name: string,
  assetClass: AssetClass,
  anchor: number,
  vol: number,
  drift: number,
  loadings: Partial<Record<Factor, number>>,
): Instrument {
  return { symbol, name, assetClass, secType: "ETF", multiplier: 1, anchor, vol, drift, loadings };
}

export const SLEEVE_MOMENTUM = "ETF Momentum";
export const SLEEVE_HEDGE = "CRB Hedge";

// ── deterministic randomness ─────────────────────────────────────────────

function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return h >>> 0;
}

function rng(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function normal(key: string): number {
  const r = rng(hash(key));
  const u = Math.max(r(), 1e-12);
  return Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * r());
}

function normals(key: string, n: number): number[] {
  const r = rng(hash(key));
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const u = Math.max(r(), 1e-12);
    out.push(Math.sqrt(-2 * Math.log(u)) * Math.cos(2 * Math.PI * r()));
  }
  return out;
}

// Picks one market path for the demo. Chosen for an unremarkable year
// (S&P up mid-single digits to low teens), not for strategy performance.
const SEED = "demo-v7";

const factorShock = (f: Factor, date: string) => normal(`${SEED}|factor|${f}|${date}`) * FACTOR_SD[f];

export function dailyReturn(inst: Instrument, date: string): number {
  if (inst.leveraged) {
    const base = INSTRUMENTS.get(inst.leveraged.underlying)!;
    return inst.leveraged.leverage * dailyReturn(base, date) + inst.drift / 252;
  }
  let systematic = 0;
  let sysVar = 0;
  for (const [f, beta] of Object.entries(inst.loadings) as [Factor, number][]) {
    systematic += beta * factorShock(f, date);
    sysVar += (beta * FACTOR_SD[f]) ** 2;
  }
  const totalDaily = inst.vol / Math.sqrt(252);
  const idio = Math.sqrt(Math.max(totalDaily ** 2 - sysVar, (0.15 * totalDaily) ** 2));
  const symbol = inst.symbol === HEDGE.symbol ? "SPY" : inst.symbol; // the hedge tracks the S&P
  return inst.drift / 252 + systematic + idio * normal(`${SEED}|idio|${symbol}|${date}`);
}

// ── daily history ────────────────────────────────────────────────────────

export interface Holding {
  qty: number;
  avgCost: number;
}

export interface Trade {
  date: string;
  symbol: string;
  qty: number; // signed
  price: number;
  reason: "rebalance" | "drift" | "cash" | "hedge";
}

export interface DayRecord {
  date: string;
  nav: number;
  cash: number;
  holdings: Map<string, Holding>;
  targets: Map<string, number>;
  trades: Trade[];
  ret: number;
}

export interface History {
  dates: string[]; // from HISTORY_START
  close: Map<string, number[]>; // symbol -> closes aligned with dates
  book: DayRecord[]; // from INCEPTION
  bookIndex: Map<string, number>;
}

const ALL = [...ETFS, HEDGE, ...EXTRA];
export const INSTRUMENTS = new Map(ALL.map((i) => [i.symbol, i]));

const cache = new Map<string, History>();

/** Book history through the close of `lastDate` (a trading day). */
export function history(lastDate: string): History {
  const hit = cache.get(lastDate);
  if (hit) return hit;
  const dates = tradingDays(HISTORY_START, lastDate);
  const close = new Map<string, number[]>();
  for (const inst of ALL) {
    const px = [inst.anchor];
    for (let i = 1; i < dates.length; i++) px.push(px[i - 1]! * Math.exp(dailyReturn(inst, dates[i]!)));
    close.set(inst.symbol, px);
  }
  // Rescale so each path passes through its anchor (a real close), keeping
  // demo price levels realistic around today.
  const a = dates.indexOf(ANCHOR_DATE);
  if (a >= 0) {
    for (const inst of ALL) {
      const px = close.get(inst.symbol)!;
      const k = inst.anchor / px[a]!;
      close.set(
        inst.symbol,
        px.map((p) => p * k),
      );
    }
  }
  // The hedge future tracks the S&P: same path, its own level.
  const spy = close.get("SPY")!;
  const spyAnchor = spy[a >= 0 ? a : 0]!;
  close.set(
    HEDGE.symbol,
    spy.map((p) => (p / spyAnchor) * HEDGE.anchor),
  );

  const book: DayRecord[] = [];
  const startIdx = dates.indexOf(INCEPTION);
  let cash = START_NAV;
  let holdings = new Map<string, Holding>();
  let targets = new Map<string, number>();
  let lastTradeIdx = -10;

  for (let i = startIdx; i < dates.length; i++) {
    const date = dates[i]!;
    const px = (s: string) => close.get(s)![i]!;
    const prevPx = (s: string) => close.get(s)![i - 1]!;
    // 1. carry: cash interest and futures variation margin
    if (book.length > 0) {
      cash *= 1 + CASH_YIELD / 252;
      const h = holdings.get(HEDGE.symbol);
      if (h) cash += h.qty * HEDGE.multiplier * (px(HEDGE.symbol) - prevPx(HEDGE.symbol));
    }
    const navBefore = navOf(cash, holdings, px);
    // 2. decide trades at the close
    const trades: Trade[] = [];
    const monthStart = book.length === 0 || date.slice(0, 7) !== book[book.length - 1]!.date.slice(0, 7);
    if (monthStart) {
      targets = momentumTargets(close, i);
      for (const t of rebalanceTo(targets, holdings, navBefore, px)) trades.push({ ...t, date, reason: "rebalance" });
      lastTradeIdx = i;
    } else if (cash < -0.01 * navBefore) {
      // Hedge losses come out of cash; trim back to target instead of borrowing.
      for (const t of rebalanceTo(targets, holdings, navBefore, px)) trades.push({ ...t, date, reason: "cash" });
      lastTradeIdx = i;
    } else if (i - lastTradeIdx >= 3) {
      const drifted = [...targets].filter(([s, w]) => {
        const qty = holdings.get(s)?.qty ?? 0;
        return Math.abs((qty * px(s)) / navBefore - w) > DRIFT_BAND;
      });
      if (drifted.length > 0) {
        const partial = new Map(drifted);
        for (const t of rebalanceTo(partial, holdings, navBefore, px)) trades.push({ ...t, date, reason: "drift" });
        lastTradeIdx = i;
      }
    }
    // hedge: keep short S&P notional near HEDGE_RATIO of NAV
    const hedgeQty = -Math.round((HEDGE_RATIO * navBefore) / (px(HEDGE.symbol) * HEDGE.multiplier));
    const curHedge = holdings.get(HEDGE.symbol)?.qty ?? 0;
    if (hedgeQty !== curHedge && (monthStart || Math.abs(hedgeQty - curHedge) >= 2)) {
      trades.push({ date, symbol: HEDGE.symbol, qty: hedgeQty - curHedge, price: px(HEDGE.symbol), reason: "hedge" });
    }
    // 3. apply trades
    holdings = new Map([...holdings].map(([s, h]) => [s, { ...h }]));
    for (const t of trades) cash -= applyTrade(holdings, t);
    const nav = navOf(cash, holdings, px);
    const prevNav = book.length > 0 ? book[book.length - 1]!.nav : START_NAV;
    book.push({ date, nav, cash, holdings, targets, trades, ret: nav / prevNav - 1 });
  }

  const result: History = { dates, close, book, bookIndex: new Map(book.map((d, i) => [d.date, i])) };
  cache.set(lastDate, result);
  return result;
}

function navOf(cash: number, holdings: Map<string, Holding>, px: (s: string) => number): number {
  let nav = cash;
  for (const [s, h] of holdings) if (s !== HEDGE.symbol) nav += h.qty * px(s);
  return nav;
}

/** Cash impact of a trade (positive = cash spent); updates holdings in place. */
function applyTrade(holdings: Map<string, Holding>, t: Trade): number {
  const inst = INSTRUMENTS.get(t.symbol)!;
  const h = holdings.get(t.symbol) ?? { qty: 0, avgCost: t.price };
  const notional = Math.abs(t.qty) * t.price * inst.multiplier;
  const commission =
    inst.secType === "FUT" ? Math.abs(t.qty) * 0.62 : Math.max(1, Math.abs(t.qty) * COMMISSION_PER_SHARE);
  const costs = commission + notional * SLIPPAGE;
  const newQty = h.qty + t.qty;
  let avgCost = h.avgCost;
  if (h.qty === 0 || Math.sign(t.qty) === Math.sign(h.qty)) {
    avgCost = (h.avgCost * h.qty + t.price * t.qty) / newQty; // adding to the position
  } else if (Math.sign(newQty) === -Math.sign(h.qty)) {
    avgCost = t.price; // flipped through zero: the remainder opened at this price
  } // reducing keeps the original cost
  if (newQty === 0) holdings.delete(t.symbol);
  else holdings.set(t.symbol, { qty: newQty, avgCost });
  // Futures are margined: no notional changes hands.
  return (inst.secType === "FUT" ? 0 : t.qty * t.price) + costs;
}

function rebalanceTo(
  targets: Map<string, number>,
  holdings: Map<string, Holding>,
  nav: number,
  px: (s: string) => number,
): Omit<Trade, "date" | "reason">[] {
  const out: Omit<Trade, "date" | "reason">[] = [];
  const symbols = new Set([...targets.keys()]);
  for (const s of symbols) {
    const target = Math.floor(((targets.get(s) ?? 0) * nav) / px(s));
    const qty = target - (holdings.get(s)?.qty ?? 0);
    if (qty !== 0 && Math.abs(qty * px(s)) >= 1000) out.push({ symbol: s, qty, price: px(s) });
  }
  // sells first so cash is available
  return out.sort((a, b) => a.qty - b.qty);
}

/** Momentum tilt: all names held, tilted toward 6-month winners (1% floor, 12% cap). */
export function momentumTargets(close: Map<string, number[]>, i: number): Map<string, number> {
  const scores = ETFS.map((inst) => {
    const px = close.get(inst.symbol)!;
    return { s: inst.symbol, m: px[i]! / px[Math.max(0, i - MOMENTUM_LOOKBACK)]! - 1 };
  });
  const mu = scores.reduce((a, b) => a + b.m, 0) / scores.length;
  const sd = Math.sqrt(scores.reduce((a, b) => a + (b.m - mu) ** 2, 0) / scores.length) || 1;
  let w = scores.map(({ s, m }) => ({ s, w: Math.exp(1.2 * ((m - mu) / sd)) }));
  const budget = 1 - CASH_BUFFER;
  for (let iter = 0; iter < 8; iter++) {
    const total = w.reduce((a, b) => a + b.w, 0);
    w = w.map(({ s, w: x }) => ({ s, w: Math.min(0.12, Math.max(0.01, (x / total) * budget)) }));
  }
  const total = w.reduce((a, b) => a + b.w, 0);
  return new Map(w.map(({ s, w: x }) => [s, (x / total) * budget]));
}

// ── intraday ─────────────────────────────────────────────────────────────

export const SESSION_MINUTES = 390;

/** Minutes since the open for `at` on `date`, clamped to the session. */
export function sessionMinute(date: string, at: Date): number {
  const open = etTime(date, OPEN).getTime();
  const close = etTime(date, closeTime(date)).getTime();
  const m = Math.floor((Math.min(Math.max(at.getTime(), open), close) - open) / 60_000);
  return Math.max(0, m);
}

export function sessionLength(date: string): number {
  return Math.round((etTime(date, closeTime(date)).getTime() - etTime(date, OPEN).getTime()) / 60_000);
}

const bridgeCache = new Map<string, Float64Array>();

/**
 * Log-price path (relative to the previous close) for each minute of the
 * session, a Brownian bridge that ends exactly at the day's close return.
 */
export function intradayPath(symbol: string, date: string, dayLogReturn: number): Float64Array {
  const key = `${symbol}|${date}`;
  const hit = bridgeCache.get(key);
  if (hit) return hit;
  const n = sessionLength(date);
  const inst = INSTRUMENTS.get(symbol)!;
  const minuteSd = inst.vol / Math.sqrt(252 * SESSION_MINUTES);
  // reuse the S&P path for its future so they move together
  const z = normals(`${SEED}|intraday|${symbol === HEDGE.symbol ? "SPY" : symbol}|${date}`, n);
  const walk = new Float64Array(n + 1);
  for (let k = 1; k <= n; k++) walk[k] = walk[k - 1]! + z[k - 1]! * minuteSd;
  const path = new Float64Array(n + 1);
  for (let k = 0; k <= n; k++) path[k] = walk[k]! - (k / n) * (walk[n]! - dayLogReturn);
  bridgeCache.set(key, path);
  return path;
}

// ── views used by the handlers ───────────────────────────────────────────

export interface Snapshot {
  tradingDate: string;
  live: boolean; // session in progress
  minute: number; // minutes since open (session length when complete)
  prevDate: string;
  record: DayRecord; // book after the latest completed close
  prevRecord: DayRecord;
  price: (symbol: string) => number; // live or closing price
  prevClose: (symbol: string) => number;
  hist: History;
}

/**
 * Book state at `now`. When a session is in progress, holdings are the
 * previous close's and prices move along the intraday path; trades
 * scheduled for today's close have not happened yet.
 */
export function snapshot(tradingDate: string, live: boolean, now: Date): Snapshot {
  const hist = history(tradingDate);
  const minute = live ? sessionMinute(tradingDate, now) : sessionLength(tradingDate);
  const idx = hist.dates.length - 1; // tradingDate
  const prevDate = prevTradingDay(tradingDate);
  const bi = hist.bookIndex.get(tradingDate)!;
  const record = live ? hist.book[bi - 1]! : hist.book[bi]!;
  const prevRecord = hist.book[bi - 1]!; // holdings during the session
  const prevClose = (s: string) => hist.close.get(s)![idx - 1]!;
  const closeToday = (s: string) => hist.close.get(s)![idx]!;
  const price = (s: string) => {
    if (!live) return closeToday(s);
    const path = intradayPath(s, tradingDate, Math.log(closeToday(s) / prevClose(s)));
    return prevClose(s) * Math.exp(path[minute]!);
  };
  return { tradingDate, live, minute, prevDate, record, prevRecord, price, prevClose, hist };
}

export function navAt(snap: Snapshot): number {
  const { record } = snap;
  let nav = record.cash;
  for (const [s, h] of record.holdings) {
    if (s === HEDGE.symbol) nav += h.qty * HEDGE.multiplier * (snap.price(s) - snap.prevClose(s));
    else nav += h.qty * snap.price(s);
  }
  if (!snap.live) {
    // Completed session: record.cash already includes today's hedge P&L.
    nav = record.nav;
  }
  return nav;
}

export function isRebalanceDay(date: string): boolean {
  return isTradingDay(date) && date.slice(0, 7) !== prevTradingDay(date).slice(0, 7);
}

export { addDays, nextTradingDay, prevTradingDay };
