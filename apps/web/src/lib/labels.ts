import type { Schemas } from "../api/client";
import { num, pct } from "./format";

/** Fewer daily returns than this and vol, Sharpe, beta and the like are noise; the API sends null. */
export const MIN_RATIO_OBS = 20;

export const ASSET_CLASS_LABEL: Record<Schemas["AssetClass"], string> = {
  equity: "Equities",
  fixed_income: "Treasuries",
  commodity: "Commodities",
  crypto: "Crypto",
  volatility: "Volatility",
  cash: "Cash & T-bills",
  index_future: "Index futures",
};

/** Shares of NAV closer to zero than this are float noise; the API's mandate limits allow the same. */
const NOISE = 1e-9;

export function limitText(value: number, unit: Schemas["LimitCheck"]["unit"]): string {
  if (unit === "pct") return pct(value, pctDecimals(value));
  if (unit === "ratio") return num(value, 2);
  return num(value, 0);
}

/**
 * One decimal, or as many as the first digit of a small share needs, so a value that
 * isn't zero never prints as 0.0%: a $400 margin loan on $1M of NAV is −0.04%.
 */
function pctDecimals(value: number): number {
  const abs = Math.abs(value) * 100;
  if (abs >= 0.05 || abs < NOISE * 100) return 1;
  return Math.ceil(-Math.log10(abs));
}
