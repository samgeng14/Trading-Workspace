import { describe, expect, it } from "vitest";
import { bps, compact, countdown, dateLong, datelineET, direction, MINUS, money, num, pct, price } from "./format";

describe("format", () => {
  it("formats money with grouping, sign and true minus", () => {
    expect(money(1002345.4)).toBe("$1,002,345");
    expect(money(-12345.6, { sign: true })).toBe(`${MINUS}$12,346`);
    expect(money(12345.6, { sign: true })).toBe("+$12,346");
    expect(money(0.2, { sign: true })).toBe("$0");
    expect(money(1_234_567, { compact: true })).toBe("$1.23M");
    expect(money(null)).toBe("—");
  });

  it("formats fractions as percent and basis points", () => {
    expect(pct(0.012345)).toBe("1.23%");
    expect(pct(-0.012345, 2, true)).toBe(`${MINUS}1.23%`);
    expect(pct(0.0001, 1, true)).toBe("0.0%"); // rounds to zero: no sign
    expect(bps(0.0012)).toBe("+12 bp");
    expect(bps(-0.00004)).toBe("0 bp");
  });

  it("formats prices and plain numbers", () => {
    expect(price(733.834)).toBe("733.83");
    expect(price(4.1234)).toBe("4.123");
    expect(num(-1500, 0, true)).toBe(`${MINUS}1,500`);
    expect(compact(15_400)).toBe("15.4K");
    expect(compact(950)).toBe("950");
  });

  it("classifies direction", () => {
    expect(direction(0.01)).toBe("up");
    expect(direction(-0.01)).toBe("down");
    expect(direction(0)).toBe("flat");
    expect(direction(Number.NaN)).toBe("flat");
  });

  it("formats dates and countdowns", () => {
    expect(dateLong("2026-10-09")).toBe("Fri, Oct 9, 2026");
    expect(countdown(2 * 3600_000 + 13 * 60_000)).toBe("2h 13m");
    expect(countdown(65_000)).toBe("1m 05s");
    expect(countdown(3 * 86_400_000 + 3600_000)).toBe("3d 1h");
    expect(countdown(-1)).toBe("now");
  });
});

describe("datelineET", () => {
  it("spells the exchange date out, as a newspaper dateline", () => {
    expect(datelineET("2026-10-12T18:00:00Z")).toBe("Monday, October 12, 2026");
  });
  it("uses New York's date, not UTC's, late in the evening", () => {
    expect(datelineET("2026-10-13T02:30:00Z")).toBe("Monday, October 12, 2026");
  });
});
