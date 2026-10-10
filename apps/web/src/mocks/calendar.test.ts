import { describe, expect, it } from "vitest";
import {
  closeTime,
  etDate,
  etTime,
  isTradingDay,
  nextTradingDay,
  prevTradingDay,
  sessionState,
  tradingDays,
} from "./calendar";

describe("calendar", () => {
  it("knows weekends, holidays and early closes", () => {
    expect(isTradingDay("2026-10-09")).toBe(true); // Friday
    expect(isTradingDay("2026-10-10")).toBe(false); // Saturday
    expect(isTradingDay("2026-07-03")).toBe(false); // Independence Day observed
    expect(isTradingDay("2026-04-03")).toBe(false); // Good Friday
    expect(closeTime("2026-11-27")).toBe("13:00");
    expect(closeTime("2026-11-30")).toBe("16:00");
  });

  it("steps across weekends and holidays", () => {
    expect(nextTradingDay("2026-10-09")).toBe("2026-10-12");
    expect(prevTradingDay("2026-07-06")).toBe("2026-07-02");
    expect(tradingDays("2026-10-05", "2026-10-11")).toHaveLength(5);
  });

  it("converts ET wall time across DST", () => {
    expect(etTime("2026-10-09", "09:30").toISOString()).toBe("2026-10-09T13:30:00.000Z"); // EDT
    expect(etTime("2026-12-01", "09:30").toISOString()).toBe("2026-12-01T14:30:00.000Z"); // EST
    expect(etDate(new Date("2026-10-10T03:00:00Z"))).toBe("2026-10-09");
  });

  it("reports the session", () => {
    const open = sessionState(new Date("2026-10-09T15:00:00Z")); // 11:00 ET Friday
    expect(open.session).toBe("open");
    expect(open.nextChange).toBe("closes");
    expect(open.nextChangeAt.toISOString()).toBe("2026-10-09T20:00:00.000Z");

    const weekend = sessionState(new Date("2026-10-10T16:00:00Z")); // Saturday
    expect(weekend.session).toBe("closed");
    expect(weekend.tradingDate).toBe("2026-10-09");
    expect(weekend.nextChangeAt.toISOString()).toBe("2026-10-12T13:30:00.000Z");

    const pre = sessionState(new Date("2026-10-12T12:00:00Z")); // 08:00 ET Monday
    expect(pre.session).toBe("pre");
    expect(pre.tradingDate).toBe("2026-10-09");

    const post = sessionState(new Date("2026-10-09T21:00:00Z")); // 17:00 ET
    expect(post.session).toBe("post");
    expect(post.tradingDate).toBe("2026-10-09");
  });
});
