// NYSE trading calendar for the demo backend. The real backend uses an
// exchange-calendar library; this covers 2025-2027, which is all the demo
// needs (the paper account started 2025-10-15).

const HOLIDAYS = new Set([
  // 2025
  "2025-01-01",
  "2025-01-09",
  "2025-01-20",
  "2025-02-17",
  "2025-04-18",
  "2025-05-26",
  "2025-06-19",
  "2025-07-04",
  "2025-09-01",
  "2025-11-27",
  "2025-12-25",
  // 2026
  "2026-01-01",
  "2026-01-19",
  "2026-02-16",
  "2026-04-03",
  "2026-05-25",
  "2026-06-19",
  "2026-07-03",
  "2026-09-07",
  "2026-11-26",
  "2026-12-25",
  // 2027
  "2027-01-01",
  "2027-01-18",
  "2027-02-15",
  "2027-03-26",
  "2027-05-31",
  "2027-06-18",
  "2027-07-05",
  "2027-09-06",
  "2027-11-25",
  "2027-12-24",
]);

// Sessions that close at 13:00 ET.
const EARLY_CLOSES = new Set(["2025-07-03", "2025-11-28", "2025-12-24", "2026-11-27", "2026-12-24", "2027-11-26"]);

export const OPEN = "09:30";
export const PRE_OPEN = "04:00";
export const POST_CLOSE = "20:00";

export function closeTime(date: string): string {
  return EARLY_CLOSES.has(date) ? "13:00" : "16:00";
}

export function addDays(date: string, days: number): string {
  const d = new Date(date + "T00:00:00Z");
  d.setUTCDate(d.getUTCDate() + days);
  return d.toISOString().slice(0, 10);
}

export function isTradingDay(date: string): boolean {
  const dow = new Date(date + "T00:00:00Z").getUTCDay();
  return dow !== 0 && dow !== 6 && !HOLIDAYS.has(date);
}

export function nextTradingDay(date: string): string {
  let d = addDays(date, 1);
  while (!isTradingDay(d)) d = addDays(d, 1);
  return d;
}

export function prevTradingDay(date: string): string {
  let d = addDays(date, -1);
  while (!isTradingDay(d)) d = addDays(d, -1);
  return d;
}

/** Trading days in [start, end], inclusive. */
export function tradingDays(start: string, end: string): string[] {
  const out: string[] = [];
  for (let d = start; d <= end; d = addDays(d, 1)) if (isTradingDay(d)) out.push(d);
  return out;
}

// ── Eastern-time conversions ────────────────────────────────────────────

const partsFmt = new Intl.DateTimeFormat("en-US", {
  timeZone: "America/New_York",
  hourCycle: "h23",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

function etParts(at: Date): Record<string, number> {
  const out: Record<string, number> = {};
  for (const p of partsFmt.formatToParts(at)) if (p.type !== "literal") out[p.type] = Number(p.value);
  return out;
}

function etOffsetMs(at: Date): number {
  const p = etParts(at);
  const asUtc = Date.UTC(p.year!, p.month! - 1, p.day!, p.hour!, p.minute!, p.second!);
  return asUtc - Math.floor(at.getTime() / 1000) * 1000;
}

/** The instant at which the ET wall clock reads `hhmm` on `date`. */
export function etTime(date: string, hhmm: string): Date {
  const [y, m, d] = date.split("-").map(Number) as [number, number, number];
  const [hh, mm] = hhmm.split(":").map(Number) as [number, number];
  const guess = Date.UTC(y, m - 1, d, hh, mm);
  return new Date(guess - etOffsetMs(new Date(guess)));
}

/** Calendar date in ET for an instant. */
export function etDate(at: Date): string {
  const p = etParts(at);
  return `${p.year}-${String(p.month).padStart(2, "0")}-${String(p.day).padStart(2, "0")}`;
}

export type Session = "pre" | "open" | "post" | "closed";

export interface SessionState {
  session: Session;
  /** Current session's date, or the last completed one. */
  tradingDate: string;
  nextChangeAt: Date;
  nextChange: "opens" | "closes";
}

export function sessionState(now: Date): SessionState {
  const today = etDate(now);
  if (isTradingDay(today)) {
    const open = etTime(today, OPEN);
    const close = etTime(today, closeTime(today));
    if (now < open) {
      const pre = now >= etTime(today, PRE_OPEN);
      return {
        session: pre ? "pre" : "closed",
        tradingDate: prevTradingDay(today),
        nextChangeAt: open,
        nextChange: "opens",
      };
    }
    if (now < close) return { session: "open", tradingDate: today, nextChangeAt: close, nextChange: "closes" };
    const next = nextTradingDay(today);
    const post = now < etTime(today, POST_CLOSE);
    return {
      session: post ? "post" : "closed",
      tradingDate: today,
      nextChangeAt: etTime(next, OPEN),
      nextChange: "opens",
    };
  }
  const next = nextTradingDay(today);
  return {
    session: "closed",
    tradingDate: prevTradingDay(today),
    nextChangeAt: etTime(next, OPEN),
    nextChange: "opens",
  };
}
