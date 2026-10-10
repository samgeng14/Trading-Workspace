import { describe, expect, it } from "vitest";
import type { Schemas } from "../api/client";
import { gate, readiness } from "./readiness";

const status = (over: Partial<Schemas["SystemStatus"]> = {}): Schemas["SystemStatus"] => ({
  mode: "paper",
  account: { id: "DU1234567", base_currency: "USD" },
  as_of: "2026-10-09T18:00:00Z",
  market: {
    session: "open",
    trading_date: "2026-10-09",
    next_change_at: "2026-10-09T20:00:00Z",
    next_change: "closes",
  },
  components: [
    { id: "gateway", label: "IB Gateway", status: "ok", detail: "127.0.0.1:4002" },
    { id: "market_data", label: "Market data", status: "ok", detail: "Live" },
  ],
  kill_switch: { armed: false },
  schedule: [],
  ...over,
});

const check = (id: string, s: "pass" | "warn" | "fail"): Schemas["PreTradeCheck"] => ({
  id,
  label: id,
  status: s,
  detail: `${id} detail`,
});

const preview = (checks: Schemas["PreTradeCheck"][]): Schemas["RebalancePreview"] => ({
  as_of: "2026-10-09T18:00:00Z",
  sleeve: "momentum",
  targets_date: "2026-10-08",
  execution: "MOC",
  rows: [],
  totals: { buy_notional: 0, sell_notional: 0, turnover: 0, est_cost: 0, orders: 0 },
  checks,
  can_submit: checks.every((c) => c.status !== "fail"),
});

const limit = (id: string, s: "ok" | "warning" | "breach"): Schemas["LimitCheck"] => ({
  id,
  label: id,
  value: 1,
  limit: 2,
  unit: "pct",
  status: s,
});

const risk = (limits: Schemas["LimitCheck"][]) => ({ limits }) as unknown as Schemas["RiskReport"];

describe("readiness", () => {
  it("is clear when everything passes", () => {
    const r = readiness({
      status: status(),
      preview: preview([check("gross", "pass"), check("duplicate", "pass")]),
      risk: risk([limit("dd", "ok")]),
    });
    expect(r.verdict).toBe("clear");
    expect(r.blockers).toEqual([]);
    expect(r.warnings).toEqual([]);
    expect(r.checks.find((c) => c.id === "pretrade")?.value).toBe("1/1 pass");
  });

  it("blocks on an armed kill switch, a failing pre-trade check or a limit breach", () => {
    const r = readiness({
      status: status({ kill_switch: { armed: true, reason: "drill" } }),
      preview: preview([check("gross", "fail"), check("turnover", "warn")]),
      risk: risk([limit("dd", "breach"), limit("beta", "warning")]),
    });
    expect(r.verdict).toBe("blocked");
    expect(r.blockers.map((b) => b.id)).toEqual(["kill_switch", "pretrade.gross", "limit.dd"]);
    expect(r.warnings.map((w) => w.id)).toEqual(["pretrade.turnover", "limit.beta"]);
    expect(r.blockers[0]!.detail).toContain("drill");
    expect(r.blockers[2]!.detail).toBe("100.0% vs limit 200.0%");
  });

  it("treats an already-submitted session as done, not blocked", () => {
    const r = readiness({
      status: status(),
      preview: preview([check("gross", "pass"), check("duplicate", "fail")]),
      risk: risk([]),
    });
    expect(r.verdict).toBe("clear");
    expect(r.checks.find((c) => c.id === "pretrade")).toMatchObject({ state: "ok", value: "Submitted" });
  });

  it("asks for review on degraded data and a failed daily run", () => {
    const r = readiness({
      status: status({
        components: [
          { id: "gateway", label: "IB Gateway", status: "ok", detail: "" },
          { id: "market_data", label: "Market data", status: "warning", detail: "Delayed 15 min: not shared" },
        ],
      }),
      preview: preview([]),
      risk: risk([]),
      runs: {
        runs: [{ id: "d", kind: "daily", started_at: "", ok: false, summary: "gateway down", steps: [] }],
      },
    });
    expect(r.verdict).toBe("review");
    expect(r.checks.find((c) => c.id === "market_data")?.value).toBe("Delayed 15 min");
    expect(r.warnings.map((w) => w.id)).toEqual(["market_data", "daily_run"]);
  });

  it("reports loading until the core inputs arrive, and never invents an all-clear", () => {
    const r = readiness({});
    expect(r.verdict).toBe("loading");
    expect(r.checks.every((c) => c.state === "unknown")).toBe(true);
  });
});

describe("gate", () => {
  it("says whether the next rebalance would go", () => {
    const pass = preview([check("gross", "pass"), check("duplicate", "pass")]);
    expect(gate(undefined, pass).text).toBe("…");
    expect(gate(status(), pass)).toEqual({ text: "Ready", tone: "good" });
    expect(gate(status(), preview([check("gross", "fail")])).text).toBe("Blocked");
    expect(gate(status(), preview([check("duplicate", "fail")])).text).toBe("Submitted");
    expect(gate(status({ kill_switch: { armed: true } }), pass).text).toBe("Halted");
  });
});
