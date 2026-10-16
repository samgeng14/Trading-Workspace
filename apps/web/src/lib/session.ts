import type { Schemas } from "../api/client";
import { EXCHANGE_TZ } from "./format";

const offsetFmt = new Intl.DateTimeFormat("en-US", { timeZone: EXCHANGE_TZ, timeZoneName: "longOffset" });

/** New York's UTC offset on a calendar date, as "-04:00" (EDT) or "-05:00" (EST). */
export function exchangeOffset(isoDate: string): string {
  // Noon UTC is morning in New York, after any 02:00 daylight-saving switch that day.
  const name = offsetFmt.formatToParts(new Date(`${isoDate}T12:00:00Z`)).find((p) => p.type === "timeZoneName")?.value;
  const m = name?.match(/GMT([+-]\d{2}:\d{2})/);
  return m ? m[1]! : "+00:00";
}

/** Epoch ms of a wall-clock time in New York on a calendar date: ("2026-10-09", "09:30"). */
export function exchangeTime(isoDate: string, hhmm: string): number {
  return Date.parse(`${isoDate}T${hhmm}:00${exchangeOffset(isoDate)}`);
}

/**
 * How far through the regular session the exchange is, 0 to 1, while the market is open;
 * null otherwise. It runs from 09:30 ET to the close the API reports, so early closes count.
 */
export function sessionProgress(market: Schemas["SystemStatus"]["market"] | undefined, now: Date): number | null {
  if (!market || market.session !== "open") return null;
  const open = exchangeTime(market.trading_date, "09:30");
  const close = Date.parse(market.next_change_at);
  if (!(close > open)) return null;
  return Math.min(1, Math.max(0, (now.getTime() - open) / (close - open)));
}
