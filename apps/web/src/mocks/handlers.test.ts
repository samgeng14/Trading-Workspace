import { setupServer } from "msw/node";
import { afterAll, afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import { handlers } from "./handlers";

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
    expect(summary.gross_exposure).toBeGreaterThan(1);
    expect(summary.nav_history.length).toBeLessThanOrEqual(30);

    const intraday = await get<S["IntradayPnl"]>("/portfolio/intraday");
    expect(intraday.points.length).toBeGreaterThan(0);
    expect(intraday.live).toBe(session === "open");
    const lastPnl = intraday.points.at(-1)!.pnl;
    expect(Math.abs(lastPnl - summary.day_pnl)).toBeLessThan(Math.max(5, Math.abs(summary.day_pnl) * 0.02));

    const perf = await get<S["Performance"]>("/performance?range=ITD&benchmark=SPY");
    expect(perf.series[0]!.portfolio).toBe(100);
    expect(perf.series.length).toBeGreaterThan(200);
    expect(perf.monthly.length).toBeGreaterThanOrEqual(12);
    for (const r of ["1M", "3M", "6M", "YTD", "1Y"]) {
      for (const b of ["QQQ", "6040", "BIL"]) {
        const p = await get<S["Performance"]>(`/performance?range=${r}&benchmark=${b}`);
        expect(p.series.length).toBeGreaterThan(5);
      }
    }
    expect((await fetch(BASE + "/performance?benchmark=NOPE")).status).toBe(400);

    const risk = await get<S["RiskReport"]>("/risk");
    expect(risk.limits.length).toBe(8);
    expect(risk.var_95_1d).toBeGreaterThan(0);
    expect(risk.es_975_1d).toBeGreaterThanOrEqual(risk.var_95_1d);

    await get<S["MarketStrip"]>("/market/strip");
    await get<S["OrdersResponse"]>("/trading/orders");
    const preview = await get<S["RebalancePreview"]>("/trading/rebalance/preview");
    expect(preview.rows).toHaveLength(22);
    await get<S["DoctorReport"]>("/ops/doctor");
    const runs = await get<S["RunsResponse"]>("/ops/runs");
    expect(runs.runs.length).toBeGreaterThan(5);
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
