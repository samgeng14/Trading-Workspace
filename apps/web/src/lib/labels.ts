import type { Schemas } from "../api/client";
import { num, pct } from "./format";

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
