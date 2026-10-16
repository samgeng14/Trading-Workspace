import type { Schemas } from "../api/client";

export type EquityMode = "return" | "growth" | "nav";

export const EQUITY_MODES = [
  { value: "return", label: "Return %" },
  { value: "growth", label: "Growth of $100" },
  { value: "nav", label: "NAV ($)" },
] as const;

/**
 * The two lines for a mode, and `base`, where both start.
 * Return: cumulative return since the first point (0 = flat), as IBKR's app draws it.
 * Growth: both start at 100. NAV: the account's dollars, and the benchmark as the same
 * starting NAV grown at the benchmark's return.
 */
export function equityLines(series: Schemas["Performance"]["series"], mode: EquityMode) {
  const first = series[0];
  if (mode === "return") {
    return {
      portfolio: series.map((p) => p.portfolio / 100 - 1),
      benchmark: series.map((p) => p.benchmark / 100 - 1),
      base: 0,
    };
  }
  if (mode === "growth" || !first) {
    return { portfolio: series.map((p) => p.portfolio), benchmark: series.map((p) => p.benchmark), base: 100 };
  }
  return {
    portfolio: series.map((p) => p.nav),
    benchmark: series.map((p) => (first.nav * p.benchmark) / first.benchmark),
    base: first.nav,
  };
}
