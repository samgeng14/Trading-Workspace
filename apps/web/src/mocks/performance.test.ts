import { setupServer } from "msw/node";
import { afterAll, beforeAll, describe, expect, it, vi } from "vitest";
import type { components } from "../api/schema";
import { handlers } from "./handlers";

type S = components["schemas"];
const server = setupServer(...handlers);
const BASE = "http://localhost/api/v1";

beforeAll(() => {
  server.listen({ onUnhandledRequest: "error" });
  vi.useFakeTimers({ toFake: ["Date"] });
  vi.setSystemTime(new Date("2026-10-09T21:00:00Z")); // Friday after the close
});
afterAll(() => {
  vi.useRealTimers();
  server.close();
});

const get = async <T>(path: string): Promise<T> => (await (await fetch(BASE + path)).json()) as T;

describe("demo performance series", () => {
  it("carries the simulated NAV, and growth of $100 is that NAV rebased", async () => {
    const summary = await get<S["PortfolioSummary"]>("/portfolio/summary");
    for (const range of ["1M", "ITD"]) {
      const perf = await get<S["Performance"]>(`/performance?range=${range}&benchmark=SPY`);
      const nav0 = perf.series[0]!.nav;
      for (const p of perf.series) expect(p.portfolio).toBeCloseTo((100 * p.nav) / nav0, 9);
      const byDate = new Map(perf.series.map((p) => [p.date, p.nav]));
      const shared = summary.nav_history.filter((h) => byDate.has(h.date));
      expect(shared.length).toBeGreaterThan(15);
      for (const h of shared) expect(byDate.get(h.date)).toBe(h.value);
      expect(perf.series.at(-1)!.nav).toBe(summary.nav);
    }
  });
});
