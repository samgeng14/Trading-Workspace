import { describe, expect, it } from "vitest";
import { exchangeOffset, exchangeTime, sessionProgress } from "./session";

const open = (trading_date: string, closesAt: string) =>
  ({ session: "open", trading_date, next_change: "closes", next_change_at: closesAt }) as const;

describe("exchange time", () => {
  it("follows New York's daylight saving", () => {
    expect(exchangeOffset("2026-10-09")).toBe("-04:00");
    expect(exchangeOffset("2026-12-01")).toBe("-05:00");
    // 2026-11-01 is the switch back to standard time; the open that day is EST
    expect(exchangeOffset("2026-11-01")).toBe("-05:00");
    expect(new Date(exchangeTime("2026-10-09", "09:30")).toISOString()).toBe("2026-10-09T13:30:00.000Z");
    expect(new Date(exchangeTime("2026-12-01", "09:30")).toISOString()).toBe("2026-12-01T14:30:00.000Z");
  });
});

describe("session progress", () => {
  it("runs from the open to the reported close", () => {
    const m = open("2026-10-09", "2026-10-09T20:00:00Z");
    expect(sessionProgress(m, new Date("2026-10-09T13:30:00Z"))).toBe(0);
    expect(sessionProgress(m, new Date("2026-10-09T16:45:00Z"))).toBeCloseTo(0.5);
    expect(sessionProgress(m, new Date("2026-10-09T20:00:00Z"))).toBe(1);
  });

  it("counts an early close as the end of the session", () => {
    // the day after Thanksgiving closes at 13:00 ET
    const m = open("2026-11-27", "2026-11-27T18:00:00Z");
    expect(sessionProgress(m, new Date("2026-11-27T16:15:00Z"))).toBeCloseTo(0.5);
  });

  it("is null outside the session", () => {
    expect(sessionProgress(undefined, new Date())).toBeNull();
    expect(
      sessionProgress(
        { session: "closed", trading_date: "2026-10-09", next_change: "opens", next_change_at: "2026-10-12T13:30:00Z" },
        new Date("2026-10-10T16:00:00Z"),
      ),
    ).toBeNull();
  });
});
