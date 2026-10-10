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

export function limitText(value: number, unit: Schemas["LimitCheck"]["unit"]): string {
  if (unit === "pct") return pct(value, 1);
  if (unit === "ratio") return num(value, 2);
  return num(value, 0);
}
