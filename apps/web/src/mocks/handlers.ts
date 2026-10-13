// Demo backend: implements schemas/api/v1.yaml from the simulation in
// sim.ts. Mutations (kill switch, rebalance submit, cancel, doctor run)
// live in memory for the lifetime of the tab.

import { http, HttpResponse } from "msw";
import type { components } from "../api/schema";
import {
  closeTime,
  etDate,
  etTime,
  isTradingDay,
  nextTradingDay,
  OPEN,
  prevTradingDay,
  sessionState,
} from "./calendar";
import { now } from "./clock";
import { type Book, type BookOrder, check, exposure, pct as pct1, POLICY } from "./mandate";
import {
  bookOf,
  ETFS,
  HEDGE,
  HEDGE_RATIO,
  history,
  INCEPTION,
  INSTRUMENTS,
  intradayPath,
  isRebalanceDay,
  momentumTargets,
  navAt,
  orderOf,
  pathAt,
  SAMPLE_SECONDS,
  samplerPhase,
  type Snapshot,
  sessionStep,
  SLEEVE_HEDGE,
  SLEEVE_MOMENTUM,
  SLEEVE_WEIGHT,
  snapshot,
  START_NAV,
  type Trade,
  tradeCosts,
} from "./sim";
import {
  annualizedVol,
  DAYS_PER_YEAR,
  drawdowns,
  hitRate,
  mean,
  MIN_RATIO_OBS,
  relative,
  sharpe,
  sortino,
} from "./stats";

type S = components["schemas"];

const API = "*/api/v1"; // any origin: same-origin in the browser, localhost in tests
const ACCOUNT_ID = "DU0000000";
const RF_DAILY = 0.042 / 252;
const MOC_SUBMIT = "15:40";
const MOC_CUTOFF = "15:50";

// ── mutable demo state ──────────────────────────────────────────────────

const state = {
  killSwitch: {
    armed: false,
    changed_at: "2026-09-30T20:41:00Z",
    changed_by: "demo",
    reason: undefined as string | undefined,
  },
  submitted: [] as S["Order"][],
  submittedFor: new Map<string, string>(), // execution date -> ET time of the manual submission
  cancelled: new Set<string>(),
  doctorRanAt: null as string | null,
};

// ── shared views ────────────────────────────────────────────────────────

function current(): { at: Date; snap: Snapshot; session: ReturnType<typeof sessionState> } {
  const at = now();
  const session = sessionState(at);
  const snap = snapshot(session.tradingDate, session.session === "open", at);
  return { at, snap, session };
}

const iso = (d: Date) => d.toISOString();
const sleeveOf = (symbol: string) => (symbol === HEDGE.symbol ? SLEEVE_HEDGE : SLEEVE_MOMENTUM);

const betaCache = new Map<string, number>();

/**
 * Beta to SPY measured on the last year of daily returns through the latest
 * completed close, as the real API measures it. The future is the S&P itself.
 */
function measuredBeta(snap: Snapshot, symbol: string): number {
  if (symbol === HEDGE.symbol) return 1;
  const { dates, close } = snap.hist;
  const end = snap.live ? dates.length - 2 : dates.length - 1;
  const key = `${symbol}|${dates[end]}`;
  const hit = betaCache.get(key);
  if (hit !== undefined) return hit;
  const px = close.get(symbol)!;
  const spy = close.get("SPY")!;
  const rets: number[] = [];
  const mkt: number[] = [];
  for (let i = Math.max(1, end - 251); i <= end; i++) {
    rets.push(px[i]! / px[i - 1]! - 1);
    mkt.push(spy[i]! / spy[i - 1]! - 1);
  }
  const b = relative(rets, mkt).beta;
  betaCache.set(key, b);
  return b;
}

/** The whole demo book as the engine's no-leverage check sees it, at the snapshot's prices. */
const bookNow = (snap: Snapshot, nav: number) => bookOf(nav, snap.record.cash, snap.record.holdings, snap.price);

function marketValue(snap: Snapshot, symbol: string, qty: number): number {
  return qty * INSTRUMENTS.get(symbol)!.multiplier * snap.price(symbol);
}

/** Close of the latest book record on or before `date`. */
function navOn(snap: Snapshot, date: string): number {
  const book = snap.hist.book;
  let lo = 0;
  let hi = book.length - 1;
  if (date < book[0]!.date) return START_NAV;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (book[mid]!.date <= date) lo = mid;
    else hi = mid - 1;
  }
  return book[lo]!.nav;
}

function closeOn(snap: Snapshot, symbol: string, date: string): number {
  const dates = snap.hist.dates;
  let idx = dates.length - 1;
  while (idx > 0 && dates[idx]! > date) idx--;
  return snap.hist.close.get(symbol)![idx]!;
}

function positions(snap: Snapshot, nav: number): S["Position"][] {
  const during = snap.prevRecord.holdings;
  return [...snap.record.holdings].map(([symbol, h]) => {
    const inst = INSTRUMENTS.get(symbol)!;
    const last = snap.price(symbol);
    const prev = snap.prevClose(symbol);
    const mv = marketValue(snap, symbol, h.qty);
    const heldToday = during.get(symbol)?.qty ?? 0;
    const target = snap.record.targets.get(symbol);
    return {
      symbol,
      name: inst.name,
      asset_class: inst.assetClass,
      sleeve: sleeveOf(symbol),
      sec_type: inst.secType,
      quantity: h.qty,
      multiplier: inst.multiplier,
      avg_cost: h.avgCost,
      last,
      prev_close: prev,
      market_value: mv,
      weight: mv / nav,
      target_weight: symbol === HEDGE.symbol ? -HEDGE_RATIO : (target ?? null),
      day_pnl: heldToday * inst.multiplier * (last - prev),
      day_return: last / prev - 1,
      unrealized_pnl: h.qty * inst.multiplier * (last - h.avgCost),
      unrealized_return: (Math.sign(h.qty) * (last - h.avgCost)) / h.avgCost,
      beta: measuredBeta(snap, symbol),
    };
  });
}

function exposures(pos: S["Position"][]) {
  const long = pos.filter((p) => p.weight > 0).reduce((a, p) => a + p.weight, 0);
  const short = pos.filter((p) => p.weight < 0).reduce((a, p) => a + p.weight, 0);
  const portBeta = pos.reduce((a, p) => a + p.weight * p.beta, 0);
  return { long, short, gross: long - short, net: long + short, beta: portBeta };
}

// ── /status ─────────────────────────────────────────────────────────────

const JOBS: { id: string; label: string; at: string }[] = [
  { id: "catchup", label: "Data catch-up (Flex T−1)", at: "06:00" },
  { id: "preopen", label: "Pre-open checks", at: "09:00" },
  { id: "finalize", label: "Finalize orders", at: "15:30" },
  { id: "submit", label: "Submit MOC orders", at: MOC_SUBMIT },
  { id: "bars", label: "Daily bars", at: "16:15" },
  { id: "reconcile", label: "End-of-day reconcile", at: "16:30" },
  { id: "brief", label: "Daily brief", at: "17:00" },
];

function tradesOn(date: string): Trade[] {
  const hist = history(isTradingDay(date) ? date : prevTradingDay(date));
  const rec = hist.book[hist.bookIndex.get(date) ?? -1];
  return rec ? rec.trades : [];
}

function schedule(at: Date): S["ScheduledJob"][] {
  const today = etDate(at);
  const day = isTradingDay(today) && at < etTime(today, "20:00") ? today : nextTradingDay(today);
  const rebalanceDue = isRebalanceDay(day);
  return JOBS.map((job) => {
    const when = etTime(day, job.id === "submit" && closeTime(day) === "13:00" ? "12:40" : job.at);
    const past = when <= at;
    let status: S["ScheduledJob"]["status"] = past ? "done" : "scheduled";
    let detail = "";
    if (job.id === "submit") {
      const trades = past ? tradesOn(day).filter((t) => t.reason !== "hedge") : [];
      if (past && trades.length === 0 && !rebalanceDue) {
        status = "skipped";
        detail = "Nothing due";
      } else if (past) detail = `${trades.length} orders`;
      else detail = rebalanceDue ? "Monthly rebalance due" : "Drift check";
    }
    if (job.id === "catchup") detail = past ? "All datasets current" : "";
    return { id: job.id, label: job.label, at: iso(when), status, detail };
  });
}

function status(): S["SystemStatus"] {
  const { at, session } = current();
  const sched = schedule(at);
  const next = sched.find((j) => j.status === "scheduled");
  return {
    mode: "demo",
    account: { id: ACCOUNT_ID, base_currency: "USD" },
    as_of: iso(at),
    market: {
      session: session.session,
      trading_date: session.tradingDate,
      next_change_at: iso(session.nextChangeAt),
      next_change: session.nextChange,
    },
    components: [
      { id: "gateway", label: "IB Gateway", status: "ok", detail: "Simulated paper session on 127.0.0.1:4002" },
      {
        id: "market_data",
        label: "Market data",
        status: "warning",
        detail: "Delayed 15 min: no live subscription shared to the paper account",
      },
      {
        id: "engine",
        label: "Engine",
        status: "unknown",
        detail: "C++ engine not deployed yet (Phase 3); Python runner in use",
      },
      {
        id: "scheduler",
        label: "Scheduler",
        status: "ok",
        detail: next ? `Next: ${next.label} at ${shortET(next.at)} ET` : "Idle",
      },
      {
        id: "database",
        label: "Database",
        status: "unknown",
        detail: "Postgres not configured yet (Phase 2); file store",
      },
    ],
    kill_switch: { ...state.killSwitch },
    schedule: sched,
  };
}

const shortET = (isoString: string) =>
  new Intl.DateTimeFormat("en-US", {
    timeZone: "America/New_York",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(isoString));

// ── /market/strip ───────────────────────────────────────────────────────

function strip(): S["MarketStrip"] {
  const { at, snap } = current();
  const q = (symbol: string, label: string, scale: number, kind: S["Quote"]["kind"]): S["Quote"] => {
    const last = snap.price(symbol) * scale;
    const prev = snap.prevClose(symbol) * scale;
    return { symbol, label, last, change: last - prev, change_pct: last / prev - 1, kind };
  };
  const spy = q("SPY", "S&P 500", 10.0024, "index");
  const tlt = INSTRUMENTS.get("TLT")!.anchor;
  const yieldOf = (px: number) => 4.15 - 6 * Math.log(px / tlt);
  const y = yieldOf(snap.price("TLT"));
  const yPrev = yieldOf(snap.prevClose("TLT"));
  const vixOf = (ret: number) => Math.max(10, 16.5 - ret * 600);
  const spyRet = snap.price("SPY") / snap.prevClose("SPY") - 1;
  const vix = vixOf(spyRet);
  const vixPrev = 16.5;
  return {
    as_of: iso(at),
    delayed: true,
    items: [
      spy,
      q("QQQ", "Nasdaq-100", 36.62, "index"),
      q("IWM", "Russell 2000", 10.02, "index"),
      { symbol: "VIX", label: "VIX", last: vix, change: vix - vixPrev, change_pct: vix / vixPrev - 1, kind: "index" },
      { symbol: "US10Y", label: "US 10Y", last: y, change: y - yPrev, change_pct: y / yPrev - 1, kind: "yield" },
      q("GLD", "Gold", 10.86, "price"),
      q("USO", "WTI crude", 0.478, "price"),
      q("IBIT", "Bitcoin", 1765, "price"),
    ],
  };
}

// ── /portfolio/* ────────────────────────────────────────────────────────

function summary(): S["PortfolioSummary"] {
  const { at, snap } = current();
  const nav = navAt(snap);
  const pos = positions(snap, nav);
  const ex = exposures(pos);
  const prevNav = snap.live ? snap.record.nav : snap.prevRecord.nav;
  const date = snap.tradingDate;
  const monthBase = navOn(snap, prevTradingDay(`${date.slice(0, 7)}-01`));
  const yearBase = navOn(snap, `${Number(date.slice(0, 4)) - 1}-12-31`);
  const book = snap.hist.book;
  const rets = book.slice(1).map((d) => d.ret);
  if (snap.live) rets.push(nav / prevNav - 1);
  const spyNow = snap.price("SPY");
  const navHistory = book.slice(-30).map((d) => ({ date: d.date, value: d.nav }));
  if (snap.live) navHistory.push({ date, value: nav });
  return {
    as_of: iso(at),
    inception_date: INCEPTION,
    nav,
    cash: snap.record.cash,
    day_pnl: nav - prevNav,
    day_return: nav / prevNav - 1,
    mtd_return: nav / monthBase - 1,
    ytd_return: nav / Math.max(yearBase, 1) - 1,
    itd_return: nav / START_NAV - 1,
    benchmark: BENCHMARKS[0]!,
    benchmark_itd_return: spyNow / closeOn(snap, "SPY", INCEPTION) - 1,
    long_exposure: ex.long,
    short_exposure: ex.short,
    gross_exposure: ex.gross,
    net_exposure: ex.net,
    beta: ex.beta,
    sharpe_itd: sharpe(rets, RF_DAILY),
    positions_count: pos.length,
    nav_history: navHistory.slice(-30),
    history: { first_date: INCEPTION, days: book.length, complete: true, sources: ["demo simulation"] },
  };
}

function positionsResponse(): S["PositionsResponse"] {
  const { at, snap } = current();
  const nav = navAt(snap);
  return { as_of: iso(at), nav, positions: positions(snap, nav) };
}

/**
 * Today's P&L the way the real API records it: a sample every 15 s on its own
 * clock (the first a few seconds after the open), and while the session is
 * open a last point at this second carrying the live day P&L, the same number
 * as the summary's.
 */
function intraday(): S["IntradayPnl"] {
  const { snap } = current();
  const date = snap.tradingDate;
  const open = etTime(date, OPEN);
  const close = etTime(date, closeTime(date));
  const idx = snap.hist.dates.length - 1;
  const held = [...snap.prevRecord.holdings].map(([symbol, h]) => {
    const prev = snap.prevClose(symbol);
    const closePx = snap.hist.close.get(symbol)![idx]!;
    return {
      value: h.qty * INSTRUMENTS.get(symbol)!.multiplier * prev,
      path: intradayPath(symbol, date, Math.log(closePx / prev)),
    };
  });
  const pnlAt = (step: number) => held.reduce((a, p) => a + p.value * (Math.exp(pathAt(p.path, step)) - 1), 0);
  const end = snap.live ? snap.at : close;
  const times: Date[] = [];
  for (let t = open.getTime() + samplerPhase(date) * 1000; t < end.getTime(); t += SAMPLE_SECONDS * 1000)
    times.push(new Date(t));
  const raw = times.map((t) => pnlAt(sessionStep(date, t)));
  let points: S["IntradayPnl"]["points"];
  if (snap.live) {
    points = times.map((t, k) => ({ t: iso(t), pnl: raw[k]! }));
    points.push({ t: iso(snap.at), pnl: pnlAt(snap.step) });
  } else {
    // A completed session's last sample carries the day's reported P&L: the
    // closing auction, trading costs and interest, spread over the session.
    const target = snap.record.nav - snap.prevRecord.nav;
    const span = times.at(-1)!.getTime() - open.getTime();
    const drift = target - raw.at(-1)!;
    points = times.map((t, k) => ({ t: iso(t), pnl: raw[k]! + (drift * (t.getTime() - open.getTime())) / span }));
  }
  return { trading_date: date, session_open: iso(open), session_close: iso(close), live: snap.live, points };
}

// ── /performance, /benchmarks ───────────────────────────────────────────

const BENCHMARKS: S["Benchmark"][] = [
  { id: "SPY", label: "S&P 500", description: "SPDR S&P 500 ETF, total return" },
  { id: "QQQ", label: "Nasdaq-100", description: "Invesco QQQ, total return" },
  { id: "IWM", label: "Russell 2000", description: "iShares Russell 2000, total return" },
  { id: "ACWI", label: "MSCI ACWI", description: "iShares MSCI ACWI, total return" },
  { id: "6040", label: "60/40", description: "60% SPY / 40% AGG, rebalanced daily" },
  { id: "AGG", label: "US Agg bonds", description: "iShares Core US Aggregate Bond" },
  { id: "BIL", label: "T-bills", description: "SPDR 1-3 Month T-Bill" },
];

function benchmarkReturns(snap: Snapshot, id: string, dates: string[]): number[] {
  const all = snap.hist.dates;
  const ix = (d: string) => all.indexOf(d);
  const r = (symbol: string, d: string) => {
    const px = snap.hist.close.get(symbol)!;
    const i = ix(d);
    return px[i]! / px[i - 1]! - 1;
  };
  return dates.map((d) => (id === "6040" ? 0.6 * r("SPY", d) + 0.4 * r("AGG", d) : r(id, d)));
}

function rangeStart(range: S["PerformanceRange"], end: string): string {
  const shift = (months: number) => {
    const d = new Date(end + "T00:00:00Z");
    d.setUTCMonth(d.getUTCMonth() - months);
    return d.toISOString().slice(0, 10);
  };
  const start =
    range === "1M"
      ? shift(1)
      : range === "3M"
        ? shift(3)
        : range === "6M"
          ? shift(6)
          : range === "1Y"
            ? shift(12)
            : range === "YTD"
              ? `${Number(end.slice(0, 4)) - 1}-12-31`
              : INCEPTION;
  return start < INCEPTION ? INCEPTION : start;
}

function performance(range: S["PerformanceRange"], benchmarkId: string): S["Performance"] | null {
  const bench = BENCHMARKS.find((b) => b.id === benchmarkId);
  if (!bench) return null;
  const { snap } = current();
  const book = snap.live ? snap.hist.book.slice(0, -1) : snap.hist.book;
  const end = book[book.length - 1]!.date;
  const startWanted = rangeStart(range, end);
  let baseIdx = 0;
  for (let i = 0; i < book.length; i++) if (book[i]!.date <= startWanted) baseIdx = i;
  const window = book.slice(baseIdx);
  const dates = window.map((d) => d.date);
  const portRets = window.slice(1).map((d) => d.ret);
  const benchRets = benchmarkReturns(snap, bench.id, dates.slice(1));
  let p = 100;
  let b = 100;
  const series = [{ date: dates[0]!, portfolio: 100, benchmark: 100, nav: window[0]!.nav }];
  for (let i = 1; i < dates.length; i++) {
    p *= 1 + portRets[i - 1]!;
    b *= 1 + benchRets[i - 1]!;
    series.push({ date: dates[i]!, portfolio: p, benchmark: b, nav: window[i]!.nav });
  }
  const dd = drawdowns(series.map((s) => s.portfolio));
  const rel = relative(portRets, benchRets);
  const enough = portRets.length >= MIN_RATIO_OBS;
  const best = portRets.reduce((m, r, i) => (r > m.value ? { date: dates[i + 1]!, value: r } : m), {
    date: dates[0]!,
    value: -Infinity,
  });
  const worst = portRets.reduce((m, r, i) => (r < m.value ? { date: dates[i + 1]!, value: r } : m), {
    date: dates[0]!,
    value: Infinity,
  });
  return {
    range,
    start: dates[0]!,
    end,
    benchmark: bench,
    series,
    drawdown: dates.map((date, i) => ({ date, value: dd[i]! })),
    stats: {
      observations: portRets.length,
      total_return: p / 100 - 1,
      benchmark_total_return: b / 100 - 1,
      // Same rules as the real API: no annualizing under a year, no ratios on a handful of days.
      ann_return: portRets.length >= DAYS_PER_YEAR ? (p / 100) ** (DAYS_PER_YEAR / portRets.length) - 1 : null,
      ann_vol: enough ? annualizedVol(portRets) : null,
      sharpe: enough ? sharpe(portRets, RF_DAILY) : null,
      max_drawdown: Math.min(...dd),
      beta: enough ? rel.beta : null,
      alpha_ann: enough ? rel.alpha_ann : null,
      tracking_error: enough ? rel.tracking_error : null,
      information_ratio: enough ? rel.information_ratio : null,
      sortino: enough ? sortino(portRets, RF_DAILY) : null,
      hit_rate: hitRate(portRets),
      best_day: best,
      worst_day: worst,
    },
    monthly: monthly(snap, book, bench.id),
  };
}

function monthly(snap: Snapshot, book: typeof snap.hist.book, benchId: string): S["Performance"]["monthly"] {
  const out: S["Performance"]["monthly"] = [];
  const allDates = book.map((d) => d.date);
  const benchAll = benchmarkReturns(snap, benchId, allDates.slice(1));
  let key = "";
  let pg = 1;
  let bg = 1;
  for (let i = 1; i < book.length; i++) {
    const k = book[i]!.date.slice(0, 7);
    if (k !== key && key) {
      out.push({ year: Number(key.slice(0, 4)), month: Number(key.slice(5, 7)), portfolio: pg - 1, benchmark: bg - 1 });
      pg = 1;
      bg = 1;
    }
    key = k;
    pg *= 1 + book[i]!.ret;
    bg *= 1 + benchAll[i - 1]!;
  }
  if (key)
    out.push({ year: Number(key.slice(0, 4)), month: Number(key.slice(5, 7)), portfolio: pg - 1, benchmark: bg - 1 });
  return out;
}

// ── /risk ───────────────────────────────────────────────────────────────

// The real API's scenarios (services/api dv02_api/risk.py): each holding moves by
// its measured beta times the market shock, minus duration times the rate move,
// plus the commodity or crypto shock for those asset classes.
type Shock = Partial<Record<"mkt" | "rates_bp" | "cmdty" | "crypto", number>>;
const SCENARIOS: { id: string; label: string; shock: Shock }[] = [
  { id: "spx_down_10", label: "S&P 500 −10%", shock: { mkt: -0.1 } },
  { id: "rates_up_100", label: "Rates +100 bp", shock: { rates_bp: 100 } },
  { id: "commodities_down_15", label: "Commodities −15%", shock: { cmdty: -0.15 } },
  { id: "crypto_down_30", label: "Crypto −30%", shock: { crypto: -0.3 } },
  { id: "stagflation", label: "Stagflation shock", shock: { mkt: -0.08, rates_bp: 50, cmdty: 0.1 } },
  { id: "march_2020", label: "March 2020 replay", shock: { mkt: -0.3, rates_bp: -100, cmdty: -0.4, crypto: -0.4 } },
];
/** Years, for the rates scenario: the real API's table (services/api dv02_api/universe.py). */
const DURATION: Record<string, number> = { VNQ: 4, VPU: 3, TLT: 16.5, IEI: 4.4, SHY: 1.9, BIL: 0.1, AGG: 6 };

function limit(
  id: string,
  label: string,
  value: number,
  lim: number,
  unit: S["LimitCheck"]["unit"],
  detail: string,
): S["LimitCheck"] {
  const used = lim ? value / lim : 0; // same sign as the limit counts toward it
  const status = used >= 1 ? "breach" : used >= 0.8 ? "warning" : "ok";
  return { id, label, value, limit: lim, unit, status, detail };
}

/** A mandate limit: breached only beyond it (with the engine's tolerance), never "warning". */
function mandateLimit(id: string, label: string, value: number, lim: number, detail: string, floor = false) {
  const beyond = floor ? value < lim - 1e-9 : value > lim + 1e-9;
  return { id, label, value, limit: lim, unit: "pct", status: beyond ? "breach" : "ok", detail } as S["LimitCheck"];
}

/** What each limit means for orders; the real API appends it to the limit's detail. */
function enforcement(id: string): string {
  if (id === "gross") return "the engine blocks orders that would leave the book above this; every holding counts";
  if (id === "cash") return "the engine blocks orders that would leave cash below this; every holding counts";
  if (id === "net" || id === "single_name") return "the engine blocks orders above this";
  return "monitored here, not enforced by the engine";
}

const pct0 = (v: number) => `${Math.round(v * 100)}%`;

function risk(): S["RiskReport"] {
  const { at, snap } = current();
  const nav = navAt(snap);
  const pos = positions(snap, nav);
  const ex = exposures(pos);
  const classes = new Map<S["AssetClass"], { long: number; short: number }>();
  for (const p of pos) {
    const c = classes.get(p.asset_class) ?? { long: 0, short: 0 };
    if (p.weight >= 0) c.long += p.weight;
    else c.short += p.weight;
    classes.set(p.asset_class, c);
  }
  // Historical simulation: today's weights over the last year of returns.
  const all = snap.hist.dates;
  const end = snap.live ? all.length - 2 : all.length - 1;
  const histRets: number[] = [];
  for (let i = end - 251; i <= end; i++) {
    let r = 0;
    for (const p of pos) {
      const px = snap.hist.close.get(p.symbol)!;
      r += p.weight * (px[i]! / px[i - 1]! - 1);
    }
    histRets.push(r);
  }
  const sorted = [...histRets].sort((a, b) => a - b);
  const var95 = -sorted[Math.floor(0.05 * sorted.length)]!;
  const tail = sorted.slice(0, Math.max(1, Math.floor(0.025 * sorted.length)));
  const es975 = -mean(tail);
  const forecastVol = annualizedVol(histRets.slice(-63));
  const cashLike = pos.filter((p) => p.sec_type !== "FUT").sort((a, b) => b.weight - a.weight);
  const top5 = cashLike.slice(0, 5).reduce((a, p) => a + p.weight, 0);
  const levels = snap.hist.book.map((d) => d.nav);
  levels.push(nav);
  const hwm = Math.max(...levels);
  const dayPnl = nav - (snap.live ? snap.record.nav : snap.prevRecord.nav);
  // gross, cash and the largest name as the engine's order gate measures them (mandate.ts)
  const book = exposure(bookNow(snap, nav));
  return {
    as_of: iso(at),
    nav,
    by_asset_class: [...classes].map(([asset_class, c]) => ({
      asset_class,
      long: c.long,
      short: c.short,
      net: c.long + c.short,
    })),
    by_sleeve: [
      {
        sleeve: SLEEVE_MOMENTUM,
        gross: pos.filter((p) => p.sleeve === SLEEVE_MOMENTUM).reduce((a, p) => a + Math.abs(p.weight), 0),
        net: pos.filter((p) => p.sleeve === SLEEVE_MOMENTUM).reduce((a, p) => a + p.weight, 0),
        day_pnl: pos.filter((p) => p.sleeve === SLEEVE_MOMENTUM).reduce((a, p) => a + p.day_pnl, 0),
        mandate: `Long-only ETF rotation, ${pct0(SLEEVE_WEIGHT)} of NAV; market beta allowed`,
      },
      {
        sleeve: SLEEVE_HEDGE,
        gross: pos.filter((p) => p.sleeve === SLEEVE_HEDGE).reduce((a, p) => a + Math.abs(p.weight), 0),
        net: pos.filter((p) => p.sleeve === SLEEVE_HEDGE).reduce((a, p) => a + p.weight, 0),
        day_pnl: pos.filter((p) => p.sleeve === SLEEVE_HEDGE).reduce((a, p) => a + p.day_pnl, 0),
        mandate: `Beta overlay: short S&P futures, −${pct0(HEDGE_RATIO)} of NAV`,
      },
    ],
    // The real API's limits (services/api dv02_api/risk.py report() and app.py get_risk):
    // the mandate's gross and cash breach only beyond the limit, the rest warn at 80%.
    limits: [
      mandateLimit(
        "gross",
        "Gross exposure",
        book.gross,
        POLICY.maxGross,
        "Long plus short notional, futures included",
      ),
      mandateLimit(
        "cash",
        "Cash (no margin loan)",
        book.cashRatio,
        POLICY.minCash,
        "Cash as a share of NAV; below 0 is a margin loan",
        true,
      ),
      limit("net", "Net exposure", ex.net, POLICY.maxNet, "pct", ""),
      // as the engine measures it: |net notional| per symbol, shorts and futures included
      limit("single_name", "Largest position", book.largestWeight, POLICY.maxSingleName, "pct", book.largestName),
      limit("beta", "Beta to S&P 500", ex.beta, 1.0, "ratio", "Measured on a year of daily returns"),
      limit("vol", "Forecast volatility", forecastVol, 0.12, "pct", "63-day, annualized"),
      limit("drawdown", "Drawdown from peak", nav / hwm - 1, -0.05, "pct", "From the NAV high-water mark"),
      limit("day_loss", "Day P&L", dayPnl / nav, -0.02, "pct", "Today, as a share of NAV"),
      limit("names", "Positions", pos.length, 25, "count", ""),
    ].map((l) => ({ ...l, detail: l.detail ? `${l.detail} · ${enforcement(l.id)}` : enforcement(l.id) })),
    beta: ex.beta,
    forecast_vol: forecastVol,
    target_vol: 0.1,
    var_95_1d: var95,
    es_975_1d: es975,
    top5_weight: top5,
    scenarios: SCENARIOS.map((sc) => {
      let pnl = 0;
      for (const p of pos) {
        const mv = p.market_value;
        pnl += mv * p.beta * (sc.shock.mkt ?? 0);
        pnl -= (mv * (DURATION[p.symbol] ?? 0) * (sc.shock.rates_bp ?? 0)) / 10_000;
        if (p.asset_class === "commodity") pnl += mv * (sc.shock.cmdty ?? 0);
        if (p.asset_class === "crypto") pnl += mv * (sc.shock.crypto ?? 0);
      }
      return { id: sc.id, label: sc.label, pnl, pnl_pct: pnl / nav };
    }),
  };
}

// ── /trading/* ──────────────────────────────────────────────────────────

const REASONS: Record<Trade["reason"], string> = {
  rebalance: "Monthly rebalance",
  drift: "Drift outside 3% band",
  mandate: "Gross back to target: over 98% of NAV",
  hedge: `Hedge to −${pct0(HEDGE_RATIO)} of NAV`,
};

function orders(): S["OrdersResponse"] {
  const { at, snap } = current();
  const date = snap.tradingDate;
  const submitAt = etTime(date, MOC_SUBMIT);
  const closeAt = etTime(date, closeTime(date));
  const hist = history(date);
  const rec = hist.book[hist.bookIndex.get(date)!]!;
  const idx = hist.dates.length - 1;
  const list: S["Order"][] = [];
  if (at >= submitAt) {
    const done = at >= closeAt;
    for (const t of rec.trades) {
      const id = `${date}-${t.symbol}`;
      const prev = hist.close.get(t.symbol)![idx - 1]!;
      const path = intradayPath(t.symbol, date, Math.log(t.price / prev));
      const arrival = prev * Math.exp(pathAt(path, sessionStep(date, submitAt)));
      const fill = t.price * (1 + Math.sign(t.qty) * 0.0002);
      const cancelled = state.cancelled.has(id);
      const status: S["OrderStatus"] = cancelled ? "cancelled" : done ? "filled" : "submitted";
      list.push({
        id,
        symbol: t.symbol,
        side: t.qty > 0 ? "BUY" : "SELL",
        quantity: Math.abs(t.qty),
        filled: status === "filled" ? Math.abs(t.qty) : 0,
        order_type: t.symbol === HEDGE.symbol ? "MKT" : "MOC",
        status,
        arrival_price: arrival,
        ...(status === "filled"
          ? { avg_fill_price: fill, slippage_bps: (Math.sign(t.qty) * (fill - arrival) * 10_000) / arrival }
          : {}),
        submitted_at: iso(submitAt),
        updated_at: iso(done ? new Date(closeAt.getTime() + 2_000) : submitAt),
        sleeve: sleeveOf(t.symbol),
        reason: REASONS[t.reason],
      });
    }
  }
  const manual = state.submitted.map((o) => (state.cancelled.has(o.id) ? { ...o, status: "cancelled" as const } : o));
  return { trading_date: date, orders: [...manual, ...list] };
}

function cancel(orderId: string): S["Order"] | null {
  const order = orders().orders.find((o) => o.id === orderId);
  if (!order || !["pending", "submitted", "partial"].includes(order.status)) return null;
  state.cancelled.add(orderId);
  return { ...order, status: "cancelled", updated_at: iso(now()) };
}

const dayLabel = (date: string) =>
  new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" }).format(
    new Date(date + "T00:00:00Z"),
  );

function executionDate(at: Date): string {
  const today = etDate(at);
  if (
    isTradingDay(today) &&
    at < etTime(today, closeTime(today) === "13:00" ? "12:50" : MOC_CUTOFF) &&
    at >= etTime(today, "04:00")
  )
    return today;
  return nextTradingDay(today);
}

/**
 * The engine's no-leverage check (engine/src/risk.cpp, ported in mandate.ts) on
 * the whole demo book after the basket, reported as the real API's preview
 * reports it (services/api dv02_api/rebalance.py leverage_check): every
 * holding counts, the futures hedge at notional, cash after the orders.
 */
function leverageCheck(book: Book, orders: BookOrder[]): S["PreTradeCheck"] {
  const label = "No leverage";
  const res = check(book, orders);
  if (!res.ok)
    return {
      id: "leverage",
      label,
      status: "fail",
      detail: res.breaches
        .filter((b) => b.blocking)
        .map((b) => b.message)
        .join("; "),
    };
  if (res.breaches.length)
    return { id: "leverage", label, status: "warn", detail: res.breaches.map((b) => b.message).join("; ") };
  return {
    id: "leverage",
    label,
    status: "pass",
    detail: `gross ${pct1(res.before.gross)} → ${pct1(res.after.gross)} of NAV, cash ${apiMoney(res.after.cash)} after; every holding counted`,
  };
}

/**
 * rebalance.py's _money() and the gate's "${:,.0f}": Python rounds an exact half
 * to even, where the engine's money() rounds it away from zero.
 */
export function apiMoney(v: number): string {
  const a = Math.abs(v);
  const f = Math.floor(a);
  const r = a - f > 0.5 || (a - f === 0.5 && f % 2 === 1) ? f + 1 : f;
  return `${v < 0 ? "-$" : "$"}${r.toLocaleString("en-US")}`;
}

/**
 * The daily runner's basket gate on the model's own targets (trading-engine
 * portfolio/risk/exposure_limits.py defaults), as the real API reports it.
 */
function gateChecks(targets: Map<string, number>, rows: S["RebalancePreview"]["rows"], nav: number) {
  const label = "Engine pre-trade gate";
  const weights = [...targets];
  const gross = weights.reduce((a, [, w]) => a + Math.abs(w), 0);
  const net = weights.reduce((a, [, w]) => a + w, 0);
  const held = weights.filter(([, w]) => Math.abs(w) > 1e-6).length;
  const [maxSym, maxPos] = weights.reduce((m, x) => (Math.abs(x[1]) > Math.abs(m[1]) ? x : m), ["—", 0]);
  const fails: string[] = [];
  const warns: string[] = [];
  if (Math.abs(maxPos) > 0.25 + 1e-9)
    fails.push(`max_single_position: ${maxSym} = ${pct1(Math.abs(maxPos))} > limit 25.0%`);
  if (gross > 1 + 1e-6) fails.push(`max_gross_exposure: gross ${pct1(gross)} > limit 100.0%`);
  if (net < 0.5 - 1e-6) fails.push(`min_net_exposure: net ${pct1(net)} < floor 50.0%`);
  if (net > 1 + 1e-6) fails.push(`max_net_exposure: net ${pct1(net)} > cap 100.0%`);
  if (held < 5) fails.push(`min_holdings: only ${held} holdings (min 5)`);
  const biggest = rows.reduce(
    (m, r) => (Math.abs(r.est_notional) > m.dollars ? { symbol: r.symbol, dollars: Math.abs(r.est_notional) } : m),
    {
      symbol: "",
      dollars: 0,
    },
  );
  if (biggest.dollars / nav > 0.3 + 1e-9)
    warns.push(
      `max_single_trade: ${biggest.symbol} trade ${apiMoney(biggest.dollars)} = ${pct1(biggest.dollars / nav)} of NAV > limit 30.0%`,
    );
  const out: S["PreTradeCheck"][] = [
    ...fails.map((f) => ({ id: "gate", label, status: "fail" as const, detail: `exposure: ${f}` })),
    ...warns.map((w) => ({ id: "gate_warn", label, status: "warn" as const, detail: `exposure: ${w}` })),
  ];
  if (!fails.length)
    out.push({
      id: "gate",
      label,
      status: "pass",
      detail: `exposure limits, price file and NAV pass (gross after trades ${pct1(gross)})`,
    });
  return out;
}

/**
 * The rebalance preview for the book in `snap` at `at`, with the real API's
 * checks. Holdings the model doesn't target are sold at this rebalance
 * (target 0), the fund's rule since October 12; they count toward the
 * no-leverage check either way. The futures hedge is its own sleeve's and is
 * not in the basket.
 */
export function previewOf(at: Date, snap: Snapshot): S["RebalancePreview"] {
  const nav = navAt(snap);
  const hist = snap.hist;
  const closeIdx = snap.live ? hist.dates.length - 2 : hist.dates.length - 1;
  const targets = momentumTargets(hist.close, closeIdx);
  const holdings = snap.record.holdings;
  const untargeted = [...holdings.keys()].filter((s) => !targets.has(s) && INSTRUMENTS.get(s)!.secType !== "FUT");
  const row = (s: string): S["RebalancePreview"]["rows"][number] => {
    const px = snap.price(s);
    const qty = holdings.get(s)?.qty ?? 0;
    const target = targets.get(s) ?? 0;
    const targetQty = Math.floor((target * nav) / px);
    const tradeQty = targetQty - qty;
    const notional = tradeQty * px;
    // a weight change trades from $1,000 (the basket builder's minimum); a holding outside the model goes whole
    const trades = tradeQty !== 0 && (Math.abs(notional) >= 1000 || !targets.has(s));
    return {
      symbol: s,
      current_weight: (qty * px) / nav,
      target_weight: target,
      current_qty: qty,
      target_qty: targetQty,
      trade_qty: trades ? tradeQty : 0,
      est_notional: trades ? notional : 0,
      est_cost: trades ? tradeCosts(INSTRUMENTS.get(s)!, tradeQty, px) : 0,
    };
  };
  const rows = [...ETFS.map((inst) => row(inst.symbol)), ...untargeted.map(row)];
  const trading = rows.filter((r) => r.trade_qty !== 0);
  const buy = trading.filter((r) => r.est_notional > 0).reduce((a, r) => a + r.est_notional, 0);
  const sell = -trading.filter((r) => r.est_notional < 0).reduce((a, r) => a + r.est_notional, 0);
  const cost = trading.reduce((a, r) => a + r.est_cost, 0);
  const execDate = executionDate(at);
  const turnover = (buy + sell) / nav;
  const dup = state.submittedFor.get(execDate);
  const orders = trading.map((r) => ({
    ...orderOf({ symbol: r.symbol, qty: r.trade_qty }, snap.price),
    commission: r.est_cost,
  }));
  const checks: S["PreTradeCheck"][] = [
    state.killSwitch.armed
      ? {
          id: "kill_switch",
          label: "Kill switch",
          status: "fail",
          detail: `Armed: ${state.killSwitch.reason ?? "no reason given"}`,
        }
      : { id: "kill_switch", label: "Kill switch", status: "pass", detail: "Clear" },
    {
      id: "data",
      label: "Targets and prices",
      status: "pass",
      detail: `targets from the ${hist.dates[closeIdx]} close`,
    },
    ...(untargeted.length
      ? [
          {
            id: "untargeted",
            label: "Holdings outside the targets",
            status: "warn" as const,
            detail: `${untargeted.join(", ")}: not in the model, so this rebalance sells them`,
          },
        ]
      : []),
    ...gateChecks(targets, rows, nav),
    leverageCheck(bookNow(snap, nav), orders),
    trading.length
      ? {
          id: "orders",
          label: "Orders to send",
          status: "pass",
          detail: `${trading.length} orders, turnover ${pct1(turnover)} of NAV`,
        }
      : {
          id: "orders",
          label: "Orders to send",
          status: "warn",
          detail: "nothing to trade: every difference is under $1,000",
        },
    dup
      ? {
          id: "duplicate",
          label: "Duplicate guard",
          status: "fail",
          detail: `Already submitted for this session (${dup} run)`,
        }
      : { id: "duplicate", label: "Duplicate guard", status: "pass", detail: "No submission for this session yet" },
  ];
  const cutoff = closeTime(execDate) === "13:00" ? "12:50" : MOC_CUTOFF;
  return {
    as_of: iso(at),
    sleeve: SLEEVE_MOMENTUM,
    targets_date: hist.dates[closeIdx]!,
    execution: `MOC ${execDate === etDate(at) ? "today, " : ""}${dayLabel(execDate)} (cutoff ${cutoff} ET)`,
    // the real API's order: the biggest trades first
    rows: [...rows].sort((a, b) => Math.abs(b.est_notional) - Math.abs(a.est_notional)),
    totals: { buy_notional: buy, sell_notional: sell, turnover, est_cost: cost, orders: trading.length },
    checks,
    can_submit: trading.length > 0 && checks.every((c) => c.status !== "fail"),
  };
}

function preview(): S["RebalancePreview"] {
  const { at, snap } = current();
  return previewOf(at, snap);
}

function submit(): { ok: true; body: S["RebalanceResult"] } | { ok: false; body: S["Problem"] } {
  const p = preview();
  if (!p.can_submit) {
    const failed = p.checks.filter((c) => c.status === "fail").map((c) => `${c.label}: ${c.detail}`);
    return {
      ok: false,
      body: { title: "Blocked by pre-trade checks", detail: failed.join("; ") || "Nothing to trade" },
    };
  }
  const at = now();
  const execDate = executionDate(at);
  const created: S["Order"][] = p.rows
    .filter((r) => r.trade_qty !== 0)
    .map((r) => ({
      id: `manual-${execDate}-${r.symbol}`,
      symbol: r.symbol,
      side: r.trade_qty > 0 ? "BUY" : "SELL",
      quantity: Math.abs(r.trade_qty),
      filled: 0,
      order_type: "MOC",
      status: "submitted",
      submitted_at: iso(at),
      updated_at: iso(at),
      sleeve: SLEEVE_MOMENTUM,
      reason: "Manual rebalance",
    }));
  state.submitted.push(...created);
  state.submittedFor.set(execDate, shortET(iso(at)));
  return {
    ok: true,
    body: {
      submitted: created.length,
      orders: created,
      message: `${created.length} MOC orders queued for ${dayLabel(execDate)}`,
    },
  };
}

// ── /ops/* ──────────────────────────────────────────────────────────────

function doctor(): S["DoctorReport"] {
  const { at, snap } = current();
  // Without a manual run, show the 09:00 pre-open check of the latest session.
  const preOpen = etTime(snap.tradingDate, "09:00");
  const ranAt = state.doctorRanAt ?? iso(preOpen <= at ? preOpen : etTime(prevTradingDay(snap.tradingDate), "09:00"));
  const nav = navAt(snap);
  const r = (name: string, status: S["DoctorResult"]["status"], detail: string, fix = ""): S["DoctorResult"] => ({
    name,
    status,
    detail,
    fix,
  });
  return {
    ran_at: ranAt,
    profile: "paper",
    endpoint: "127.0.0.1:4002 (simulated)",
    results: [
      r("config", "PASS", "127.0.0.1:4002 (port from broker.yaml), client IDs 300-311, account: first managed account"),
      r("socket", "PASS", "4002 listening (IB Gateway paper)"),
      r("handshake", "PASS", "API server version 178, clientId 309"),
      r("account", "PASS", `${ACCOUNT_ID} (paper)`),
      r("balances", "PASS", `NLV $${Math.round(nav).toLocaleString("en-US")} USD`),
      r("positions", "PASS", `${snap.record.holdings.size} positions (1 short)`),
      r("orders", "PASS", "0 open orders, executions today only via API; history needs a Flex query"),
      r("clock", "PASS", "local clock off by 0s"),
      r("contracts", "PASS", `all ${ETFS.length} symbols qualified`),
      r("history", "PASS", `SPY 5 daily bars, last ${snap.prevDate}`),
      r(
        "quotes",
        "WARN",
        "SPY delayed 15 min",
        "Paper accounts only get real-time quotes shared from the live login: Client Portal (live user) → Settings → Paper Trading Account → share market data.",
      ),
      r("order preview", "PASS", "BUY 1 SPY: accepted"),
      r("short preview", "SKIP", "account is long SPY; a 1-share sell is not a short"),
    ],
  };
}

function runs(): S["RunsResponse"] {
  const { snap } = current();
  const dates = snap.hist.book.slice(snap.live ? -11 : -10).map((d) => d.date);
  if (snap.live) dates.pop();
  const list: S["Run"][] = dates.reverse().map((date) => {
    const failed = Number(date.slice(8)) % 7 === 1; // an occasional gateway outage, for the failure state
    const trades = tradesOn(date).filter((t) => t.reason !== "hedge");
    const start = etTime(date, "16:32");
    return {
      id: `daily-${date}`,
      kind: "daily",
      started_at: iso(start),
      finished_at: iso(new Date(start.getTime() + (failed ? 6 : 41) * 1000)),
      ok: !failed,
      dry_run: false,
      summary: failed
        ? "Append failed: IBKR connection refused at 127.0.0.1:4002"
        : trades.length > 0
          ? `${trades.length} orders submitted (${REASONS[trades[0]!.reason].toLowerCase()})`
          : "No rebalance needed",
      steps: failed
        ? [{ name: "append_daily", ok: false, detail: "ConnectionRefusedError: [Errno 61] Connect call failed" }]
        : [
            { name: "append_daily", ok: true, detail: `${ETFS.length} symbols, 1 new row each` },
            { name: "generate_targets", ok: true, detail: "momentum_tilted" },
            { name: "rebalance_decision", ok: true, detail: trades.length > 0 ? "Rebalance" : "Within bands" },
            ...(trades.length > 0
              ? [
                  { name: "build_basket", ok: true, detail: `${trades.length} orders` },
                  { name: "submit_orders", ok: true, detail: "MOC" },
                ]
              : []),
          ],
    };
  });
  return { runs: list };
}

// ── routes ──────────────────────────────────────────────────────────────

export const handlers = [
  http.get(`${API}/status`, () => HttpResponse.json(status())),
  http.get(`${API}/market/strip`, () => HttpResponse.json(strip())),
  http.get(`${API}/portfolio/summary`, () => HttpResponse.json(summary())),
  http.get(`${API}/portfolio/positions`, () => HttpResponse.json(positionsResponse())),
  http.get(`${API}/portfolio/intraday`, () => HttpResponse.json(intraday())),
  http.get(`${API}/benchmarks`, () => HttpResponse.json(BENCHMARKS)),
  http.get(`${API}/performance`, ({ request }) => {
    const url = new URL(request.url);
    const range = (url.searchParams.get("range") ?? "ITD") as S["PerformanceRange"];
    const body = performance(range, url.searchParams.get("benchmark") ?? "SPY");
    return body ? HttpResponse.json(body) : HttpResponse.json({ title: "Unknown benchmark" }, { status: 400 });
  }),
  http.get(`${API}/risk`, () => HttpResponse.json(risk())),
  http.get(`${API}/trading/orders`, () => HttpResponse.json(orders())),
  http.post(`${API}/trading/orders/:orderId/cancel`, ({ params }) => {
    const order = cancel(String(params.orderId));
    return order
      ? HttpResponse.json(order)
      : HttpResponse.json(
          { title: "Order is not cancellable", detail: "Only working orders can be cancelled." },
          { status: 409 },
        );
  }),
  http.get(`${API}/trading/rebalance/preview`, () => HttpResponse.json(preview())),
  http.post(`${API}/trading/rebalance`, async ({ request }) => {
    const body = (await request.json()) as Partial<S["RebalanceRequest"]>;
    if (body.confirm !== "SUBMIT") return HttpResponse.json({ title: "Confirmation required" }, { status: 409 });
    const result = submit();
    return result.ok ? HttpResponse.json(result.body) : HttpResponse.json(result.body, { status: 409 });
  }),
  http.get(`${API}/ops/doctor`, () => HttpResponse.json(doctor())),
  http.post(`${API}/ops/doctor/run`, () => {
    state.doctorRanAt = iso(now());
    return HttpResponse.json(doctor());
  }),
  http.post(`${API}/ops/kill-switch`, async ({ request }) => {
    const body = (await request.json()) as S["KillSwitchRequest"];
    if (body.armed && !body.reason?.trim())
      return HttpResponse.json({ title: "A reason is required to arm the kill switch" }, { status: 400 });
    state.killSwitch = {
      armed: body.armed,
      reason: body.armed ? body.reason!.trim() : undefined,
      changed_at: iso(now()),
      changed_by: "dashboard",
    };
    return HttpResponse.json({ ...state.killSwitch });
  }),
  http.get(`${API}/ops/runs`, () => HttpResponse.json(runs())),
];
