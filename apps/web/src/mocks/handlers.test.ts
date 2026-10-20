import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import { apiMoney, handlers } from "./handlers";
import { ETFS } from "./sim";

type S = components["schemas"];
const server = setupServer(...handlers);
const BASE = "http://localhost/api/v1";

beforeAll(() => server.listen({ onUnhandledRequest: "error" }));
afterAll(() => server.close());
afterEach(() => vi.useRealTimers());

async function get<T>(path: string): Promise<T> {
  const res = await fetch(BASE + path);
  expect(res.status, path).toBe(200);
  return (await res.json()) as T;
}

async function post(path: string, body?: unknown): Promise<Response> {
  return fetch(BASE + path, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

const MOMENTS: [string, string, S["MarketSession"]["session"]][] = [
  ["Saturday", "2026-10-10T16:00:00Z", "closed"],
  ["Friday 10:05 ET", "2026-10-09T14:05:00Z", "open"],
  ["Friday 15:45 ET", "2026-10-09T19:45:00Z", "open"],
  ["Friday 17:00 ET", "2026-10-09T21:00:00Z", "post"],
  ["Monday 08:00 ET", "2026-10-12T12:00:00Z", "pre"],
  // a month-start rebalance, before and after it trades
  ["Oct 1 11:00 ET", "2026-10-01T15:00:00Z", "open"],
  ["Oct 1 17:00 ET", "2026-10-01T21:00:00Z", "post"],
  // a half day
  ["Nov 27 12:00 ET", "2026-11-27T17:00:00Z", "open"],
];

describe.each(MOMENTS)("demo API at %s", (_label, at, session) => {
  it("serves consistent numbers", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(at));

    const status = await get<S["SystemStatus"]>("/status");
    expect(status.mode).toBe("demo");
    expect(status.market.session).toBe(session);
    expect(status.schedule).toHaveLength(7);

    const summary = await get<S["PortfolioSummary"]>("/portfolio/summary");
    const positions = await get<S["PositionsResponse"]>("/portfolio/positions");
    expect(positions.nav).toBeCloseTo(summary.nav, 6);
    const net = positions.positions.reduce((a, p) => a + p.weight, 0);
    expect(net).toBeCloseTo(summary.net_exposure, 9);
    const gross = positions.positions.reduce((a, p) => a + Math.abs(p.weight), 0);
    expect(gross).toBeCloseTo(summary.gross_exposure, 9);
    // no leverage: long plus short, the futures hedge at notional, inside NAV; no margin loan
    expect(summary.gross_exposure).toBeLessThanOrEqual(1);
    expect(summary.cash).toBeGreaterThanOrEqual(0);
    expect(summary.nav_history.length).toBeLessThanOrEqual(30);

    // the real API's intraday shape: a sample every 15 s from a few seconds after
    // the open, and while the session is open a live last point that is the day P&L now
    const intraday = await get<S["IntradayPnl"]>("/portfolio/intraday");
    expect(intraday.live).toBe(session === "open");
    const times = intraday.points.map((p) => Date.parse(p.t));
    const open = Date.parse(intraday.session_open);
    const samples = intraday.live ? times.slice(0, -1) : times;
    expect(samples.length).toBeGreaterThan(0);
    expect(samples[0]! - open).toBeGreaterThan(0);
    expect(samples[0]! - open).toBeLessThan(15_000);
    for (let i = 1; i < samples.length; i++) expect(samples[i]! - samples[i - 1]!).toBe(15_000);
    const lastPnl = intraday.points.at(-1)!.pnl;
    expect(lastPnl).toBeCloseTo(summary.day_pnl, 6);
    if (intraday.live) {
      expect(times.at(-1)).toBe(Math.floor(Date.parse(at) / 1000) * 1000);
      expect(times.at(-1)! - samples.at(-1)!).toBeLessThanOrEqual(15_000);
    } else {
      const close = Date.parse(intraday.session_close);
      expect(close - samples.at(-1)!).toBeGreaterThan(0);
      expect(close - samples.at(-1)!).toBeLessThanOrEqual(15_000);
    }

    const perf = await get<S["Performance"]>("/performance?range=ITD&benchmark=SPY");
    expect(perf.series[0]!.portfolio).toBe(100);
    expect(perf.series.length).toBeGreaterThan(200);
    expect(perf.monthly.length).toBeGreaterThanOrEqual(12);
    for (const r of ["1W", "MTD", "1M", "3M", "6M", "YTD", "1Y"]) {
      for (const b of ["QQQ", "6040", "BIL"]) {
        const p = await get<S["Performance"]>(`/performance?range=${r}&benchmark=${b}`);
        // on Oct 1 the month is one session old: MTD is the Sep 30 close and today's
        expect(p.series.length).toBeGreaterThan(r === "MTD" ? 1 : 5);
        expect(p.series[0]!.portfolio).toBe(100);
      }
    }
    expect((await fetch(BASE + "/performance?benchmark=NOPE")).status).toBe(400);

    const risk = await get<S["RiskReport"]>("/risk");
    // the real API's limits, in its order, with the mandate's values (services/api mandate.POLICY)
    expect(risk.limits.map((l) => [l.id, l.limit])).toEqual([
      ["gross", 1],
      ["cash", 0],
      ["net", 1],
      ["single_name", 0.25],
      ["beta", 1],
      ["vol", 0.12],
      ["drawdown", -0.05],
      ["day_loss", -0.02],
      ["names", 25],
    ]);
    const limitOf = (id: string) => risk.limits.find((l) => l.id === id)!;
    expect(limitOf("gross").value).toBeCloseTo(summary.gross_exposure, 9);
    expect(limitOf("gross").status).toBe("ok");
    expect(limitOf("cash").value).toBeCloseTo(summary.cash / summary.nav, 9);
    expect(limitOf("cash").status).toBe("ok");
    expect(limitOf("gross").detail).toBe(
      "Long plus short notional, futures included · the engine blocks orders that would leave the book above this; every holding counts",
    );
    expect(limitOf("names").detail).toBe("monitored here, not enforced by the engine");
    // the engine's largest name counts the futures hedge too
    const largest = positions.positions.reduce((m, p) => (Math.abs(p.weight) > Math.abs(m.weight) ? p : m));
    expect(limitOf("single_name").value).toBeCloseTo(Math.abs(largest.weight), 9);
    expect(limitOf("single_name").detail).toBe(`${largest.symbol} · the engine blocks orders above this`);
    // the S&P scenario moves the book by its beta, the same beta the summary shows
    expect(risk.beta).toBeCloseTo(summary.beta, 9);
    const spx = risk.scenarios.find((sc) => sc.id === "spx_down_10")!;
    expect(spx.pnl).toBeCloseTo(-0.1 * summary.beta * summary.nav, 6);
    expect(risk.var_95_1d).toBeGreaterThan(0);
    expect(risk.es_975_1d).toBeGreaterThanOrEqual(risk.var_95_1d);

    await get<S["MarketStrip"]>("/market/strip");
    await get<S["OrdersResponse"]>("/trading/orders");
    const preview = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
    expect(preview.rows.map((r) => r.symbol).sort()).toEqual(ETFS.map((e) => e.symbol).sort());
    // the real API's order (rebalance.py): the biggest trades first
    for (let i = 1; i < preview.rows.length; i++)
      expect(Math.abs(preview.rows[i - 1]!.est_notional)).toBeGreaterThanOrEqual(
        Math.abs(preview.rows[i]!.est_notional),
      );
    expect(preview.rows.map((r) => r.symbol)).not.toContain("TQQQ");
    // the normal rebalance passes the engine's no-leverage check on its own merits
    const leverage = preview.checks.find((c) => c.id === "leverage")!;
    expect(leverage.status).toBe("pass");
    expect(leverage.detail).toMatch(/^gross \d+\.\d% → \d+\.\d% of NAV, cash \$[\d,]+ after; every holding counted$/);
    expect(preview.checks.map((c) => c.id)).toEqual([
      "kill_switch",
      "data",
      "gate",
      "leverage",
      "orders",
      "execution",
      "duplicate",
    ]);
    await get<S["DoctorReport"]>("/ops/doctor");
    const runs = await get<S["RunsResponse"]>("/ops/runs");
    expect(runs.runs.length).toBeGreaterThan(5);
  });
});

// Early in the history (too short for the performance checks above): a close where
// the simulation trimmed gross back to target, and a session on the unrescaled
// prices a demo_now before May 6, 2026 shows.
describe.each([
  ["Jan 15 17:00 ET", "2026-01-15T22:00:00Z"],
  ["Mar 10 11:00 ET", "2026-03-10T15:00:00Z"],
])("demo API early on, at %s", (_label, at) => {
  it("holds the mandate and agrees with itself", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(at));
    const summary = await get<S["PortfolioSummary"]>("/portfolio/summary");
    const positions = await get<S["PositionsResponse"]>("/portfolio/positions");
    const gross = positions.positions.reduce((a, p) => a + Math.abs(p.weight), 0);
    expect(gross).toBeCloseTo(summary.gross_exposure, 9);
    expect(summary.gross_exposure).toBeLessThanOrEqual(1);
    expect(summary.cash).toBeGreaterThanOrEqual(0);
    const risk = await get<S["RiskReport"]>("/risk");
    const limitOf = (id: string) => risk.limits.find((l) => l.id === id)!;
    expect(limitOf("gross")).toMatchObject({ status: "ok", limit: 1 });
    expect(limitOf("gross").value).toBeCloseTo(summary.gross_exposure, 9);
    expect(limitOf("cash")).toMatchObject({ status: "ok", limit: 0 });
    expect(limitOf("cash").value).toBeCloseTo(summary.cash / summary.nav, 9);
    const intraday = await get<S["IntradayPnl"]>("/portfolio/intraday");
    expect(intraday.points.at(-1)!.pnl).toBeCloseTo(summary.day_pnl, 6);
    const preview = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
    expect(preview.checks.find((c) => c.id === "leverage")!.status).toBe("pass");
  });
});

describe("demo API mutations", () => {
  it("guards the kill switch, rebalance submit and cancel", async () => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date("2026-10-09T15:00:00Z")); // 11:00 ET

    expect((await post("/ops/kill-switch", { armed: true })).status).toBe(400);
    const armed = (await (await post("/ops/kill-switch", { armed: true, reason: "test" })).json()) as S["KillSwitch"];
    expect(armed.armed).toBe(true);
    let preview = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
    expect(preview.can_submit).toBe(false);
    expect((await post("/trading/rebalance", { confirm: "SUBMIT" })).status).toBe(409);
    await post("/ops/kill-switch", { armed: false });

    preview = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
    expect(preview.execution).toContain("today");
    expect((await post("/trading/rebalance", { confirm: "nope" })).status).toBe(409);
    expect(preview.can_submit).toBe(true);
    {
      const res = await post("/trading/rebalance", { confirm: "SUBMIT" });
      expect(res.status).toBe(200);
      const result = (await res.json()) as S["RebalanceResult"];
      expect(result.submitted).toBe(preview.totals.orders);
      expect((await post("/trading/rebalance", { confirm: "SUBMIT" })).status).toBe(409); // duplicate guard
      const again = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
      // the real API's words, with the time of the run that sent them
      expect(again.checks.find((c) => c.id === "duplicate")).toMatchObject({
        status: "fail",
        detail: "Already submitted for this session (11:00 run)",
      });
      const id = result.orders[0]!.id;
      const cancelled = (await (await post(`/trading/orders/${id}/cancel`)).json()) as S["Order"];
      expect(cancelled.status).toBe("cancelled");
      expect((await post(`/trading/orders/${id}/cancel`)).status).toBe(409);
    }

    const before = await get<S["DoctorReport"]>("/ops/doctor");
    vi.setSystemTime(new Date("2026-10-09T15:05:00Z"));
    const fresh = (await (await post("/ops/doctor/run")).json()) as S["DoctorReport"];
    expect(fresh.ran_at > before.ran_at).toBe(true);
  });
});

describe("the preview's dollar amounts", () => {
  it("round as Python's format does in the real API (rebalance.py _money)", () => {
    expect(apiMoney(226_080.5)).toBe("$226,080");
    expect(apiMoney(226_081.5)).toBe("$226,082");
    expect(apiMoney(999_698.51)).toBe("$999,699");
    expect(apiMoney(-12_500)).toBe("-$12,500");
    expect(apiMoney(-0.3)).toBe("-$0");
  });
});

describe("trading today", () => {
  const at = async (when: string) => {
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(new Date(when));
    return get<S["TradingDay"]>("/trading/today");
  };

  it("answers for each moment of the day", async () => {
    const saturday = await at("2026-10-10T16:00:00Z");
    expect(saturday).toMatchObject({ state: "closed", trading_date: "2026-10-12", can_rebalance: false });
    expect(saturday.detail).toBe("The next automatic run is Mon 15:40 ET.");

    // a session no other test trades in (the demo's state lasts the whole file)
    const morning = await at("2026-10-14T14:05:00Z");
    expect(morning).toMatchObject({ state: "not_yet", headline: "Not traded yet today", can_rebalance: true });
    expect(morning.detail).toBe("The automatic run is at 15:40 ET: market-on-close, filled at the Wed Oct 14 close.");
    expect(morning.next_run_at).toBe("2026-10-14T19:40:00.000Z");

    // the month-start rebalance, after the close: every order filled at it
    const after = await at("2026-10-01T21:00:00Z");
    expect(after.state).toBe("done");
    expect(after.headline).toBe("Traded at today's close (orders sent 15:40 ET)");
    expect(after.orders!.sent).toBeGreaterThan(5);
    expect(after.orders!.filled).toBe(after.orders!.sent);
    expect(after.detail).toBe(`Filled ${after.orders!.sent}/${after.orders!.sent} at the 16:00 ET close.`);
    expect(after.blocked_reason).toBe("This session's orders are already in.");
  });

  it("walks a dashboard rebalance through its steps, then waits for the close", async () => {
    const start = new Date("2026-10-08T14:05:00Z"); // Thursday 10:05 ET
    vi.useFakeTimers({ toFake: ["Date"] });
    vi.setSystemTime(start);
    expect((await post("/trading/rebalance", { confirm: "SUBMIT" })).status).toBe(200);

    const first = await get<S["TradingDay"]>("/trading/today");
    expect(first.state).toBe("running");
    expect(first.headline).toBe("Rebalancing now: reconciling with IBKR");
    expect(first.steps.map((s) => s.state)).toEqual(["running", "pending", "pending", "pending", "pending", "pending"]);

    vi.setSystemTime(new Date(start.getTime() + 3 * 600 + 10));
    const mid = await get<S["TradingDay"]>("/trading/today");
    expect(mid.headline).toBe("Rebalancing now: deciding what to trade");
    expect(mid.steps.filter((s) => s.state === "done")).toHaveLength(3);

    vi.setSystemTime(new Date(start.getTime() + 6 * 600 + 10));
    const done = await get<S["TradingDay"]>("/trading/today");
    expect(done.state).toBe("done");
    expect(done.headline).toBe("Orders in for today's close (sent 10:05 ET)");
    expect(done.steps.at(-1)).toMatchObject({ id: "fills", state: "running" });
    expect(done.orders).toMatchObject({ filled: 0, auction: "close", source: "ibkr" });
    expect(done.can_rebalance).toBe(false);
  });
});
