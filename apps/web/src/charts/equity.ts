import type { Schemas } from "../api/client";

export type EquityMode = "growth" | "nav";

export const EQUITY_MODES = [
  { value: "growth", label: "Growth of $100" },
  { value: "nav", label: "NAV ($)" },
] as const;

/**
 * The two lines for a mode. Growth: both start at 100. NAV: the account's dollars, and the
 * benchmark as the same starting NAV grown at the benchmark's return. `base` is where both start.
 */
export function equityLines(series: Schemas["Performance"]["series"], mode: EquityMode) {
  const first = series[0];
  if (mode === "growth" || !first) {
    return { portfolio: series.map((p) => p.portfolio), benchmark: series.map((p) => p.benchmark), base: 100 };
  }
  return {
    portfolio: series.map((p) => p.nav),
    benchmark: series.map((p) => (first.nav * p.benchmark) / first.benchmark),
    base: first.nav,
  };
}
