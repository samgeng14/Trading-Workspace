// Display formatting. Every number on screen goes through here so signs,
// grouping and precision are consistent. Negative values use a true minus
// sign (U+2212), which lines up with "+" in tabular figures.

export const MINUS = "−";

const grouped = (decimals: number) =>
  new Intl.NumberFormat("en-US", {
    minimumFractionDigits: decimals,
    maximumFractionDigits: decimals,
  });

const formatters = new Map<number, Intl.NumberFormat>();
function fmt(decimals: number): Intl.NumberFormat {
  let f = formatters.get(decimals);
  if (!f) {
    f = grouped(decimals);
    formatters.set(decimals, f);
  }
  return f;
}

function signPrefix(value: number, sign: boolean): string {
  if (value < 0) return MINUS;
  if (sign && value > 0) return "+";
  return "";
}

function isMissing(value: number | null | undefined): value is null | undefined {
  return value === null || value === undefined || !Number.isFinite(value);
}

export const DASH = "—";

export function num(value: number | null | undefined, decimals = 0, sign = false): string {
  if (isMissing(value)) return DASH;
  const rounded = Number(value.toFixed(decimals));
  if (rounded === 0) return fmt(decimals).format(0);
  return signPrefix(rounded, sign) + fmt(decimals).format(Math.abs(rounded));
}

export function money(
  value: number | null | undefined,
  opts: { decimals?: number; sign?: boolean; compact?: boolean } = {},
): string {
  if (isMissing(value)) return DASH;
  const { decimals = 0, sign = false, compact = false } = opts;
  if (compact) return signPrefix(value, sign) + "$" + compactAbs(Math.abs(value));
  const rounded = Number(value.toFixed(decimals));
  if (rounded === 0) return "$" + fmt(decimals).format(0);
  return signPrefix(rounded, sign) + "$" + fmt(decimals).format(Math.abs(rounded));
}

/** `value` is a fraction: 0.0123 -> "1.23%". */
export function pct(value: number | null | undefined, decimals = 2, sign = false): string {
  if (isMissing(value)) return DASH;
  return num(value * 100, decimals, sign) + "%";
}

/** `value` is a fraction: 0.0012 -> "12 bp". */
export function bps(value: number | null | undefined, sign = true): string {
  if (isMissing(value)) return DASH;
  return num(value * 10_000, 0, sign) + " bp";
}

export function price(value: number | null | undefined): string {
  if (isMissing(value)) return DASH;
  return num(value, Math.abs(value) < 10 ? 3 : 2);
}

function compactAbs(abs: number): string {
  if (abs >= 1e9) return trim(abs / 1e9) + "B";
  if (abs >= 1e6) return trim(abs / 1e6) + "M";
  if (abs >= 1e3) return trim(abs / 1e3) + "K";
  return trim(abs);
}

function trim(v: number): string {
  const s = v >= 100 ? v.toFixed(0) : v >= 10 ? v.toFixed(1) : v.toFixed(2);
  return s.replace(/\.0+$/, "").replace(/(\.\d*?)0+$/, "$1");
}

export function compact(value: number | null | undefined, sign = false): string {
  if (isMissing(value)) return DASH;
  return signPrefix(value, sign) + compactAbs(Math.abs(value));
}

export type Direction = "up" | "down" | "flat";

export function direction(value: number | null | undefined, epsilon = 0): Direction {
  if (isMissing(value) || Math.abs(value) <= epsilon) return "flat";
  return value > 0 ? "up" : "down";
}

// ── time (exchange time, America/New_York) ───────────────────────────────

export const EXCHANGE_TZ = "America/New_York";

const timeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: EXCHANGE_TZ,
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
});
const shortTimeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: EXCHANGE_TZ,
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});
const dateLongFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  weekday: "short",
  month: "short",
  day: "numeric",
  year: "numeric",
});
const dateShortFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "UTC",
  month: "short",
  day: "numeric",
});
const monthFmt = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short" });
const weekdayTimeFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: EXCHANGE_TZ,
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

export const timeET = (iso: string | Date) => timeFmt.format(new Date(iso));
export const shortTimeET = (iso: string | Date) => shortTimeFmt.format(new Date(iso));
export const weekdayTimeET = (iso: string | Date) => weekdayTimeFmt.format(new Date(iso));

/** "2026-10-09" -> "Fri, Oct 9, 2026" (calendar date, no time zone shift). */
export const dateLong = (isoDate: string) => dateLongFmt.format(new Date(isoDate + "T00:00:00Z"));
export const dateShort = (isoDate: string) => dateShortFmt.format(new Date(isoDate + "T00:00:00Z"));
export const monthName = (month: number) => monthFmt.format(new Date(Date.UTC(2000, month - 1, 1)));

export function countdown(ms: number): string {
  if (ms <= 0) return "now";
  const s = Math.floor(ms / 1000);
  const d = Math.floor(s / 86_400);
  const h = Math.floor((s % 86_400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m ${String(sec).padStart(2, "0")}s`;
}
