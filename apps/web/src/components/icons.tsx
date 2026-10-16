// Small inline icons. Status icons differ by shape as well as colour, so
// state never depends on colour alone.

import type { ReactNode } from "react";

type Health = "ok" | "warning" | "error" | "unknown";

export function StatusIcon({
  status,
  size = 10,
}: {
  status: Health | "breach" | "pass" | "warn" | "fail" | "skip";
  size?: number;
}) {
  const s = size;
  const norm =
    status === "pass"
      ? "ok"
      : status === "warn"
        ? "warning"
        : status === "fail" || status === "breach"
          ? "error"
          : status === "skip"
            ? "unknown"
            : status;
  if (norm === "ok")
    return (
      <svg width={s} height={s} viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4.5" fill="var(--status-good)" />
      </svg>
    );
  if (norm === "warning")
    return (
      <svg width={s} height={s} viewBox="0 0 10 10" aria-hidden="true">
        <path d="M5 0.6 L9.6 9.2 H0.4 Z" fill="var(--status-warning)" strokeLinejoin="round" />
      </svg>
    );
  if (norm === "error")
    return (
      <svg width={s} height={s} viewBox="0 0 10 10" aria-hidden="true">
        <rect x="0.5" y="0.5" width="9" height="9" rx="1" fill="var(--status-critical)" />
        <path d="M3 3 L7 7 M7 3 L3 7" stroke="#fff" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    );
  return (
    <svg width={s} height={s} viewBox="0 0 10 10" aria-hidden="true">
      <circle cx="5" cy="5" r="4" fill="none" stroke="var(--status-unknown)" strokeWidth="1.5" />
    </svg>
  );
}

export function SessionIcon({ session }: { session: "pre" | "open" | "post" | "closed" }) {
  if (session === "open")
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4.5" fill="var(--status-good)" />
      </svg>
    );
  if (session === "closed")
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4" fill="none" stroke="var(--ink-3)" strokeWidth="1.5" />
      </svg>
    );
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <circle cx="5" cy="5" r="4" fill="none" stroke="var(--status-warning)" strokeWidth="1.5" />
      <path d="M5 1 A4 4 0 0 1 5 9 Z" fill="var(--status-warning)" />
    </svg>
  );
}

/** A 16px line icon on a 24px grid, drawn in the current text colour. */
function Line({ children, size = 18 }: { children: ReactNode; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {children}
    </svg>
  );
}

export const CommandIcon = () => (
  <Line>
    <rect x="3" y="3" width="7.5" height="7.5" rx="2" />
    <rect x="13.5" y="3" width="7.5" height="5" rx="2" />
    <rect x="13.5" y="11" width="7.5" height="10" rx="2" />
    <rect x="3" y="13.5" width="7.5" height="7.5" rx="2" />
  </Line>
);

export const PositionsIcon = () => (
  <Line>
    <path d="M12 3 3 7.5l9 4.5 9-4.5L12 3Z" />
    <path d="m3 12 9 4.5 9-4.5" />
    <path d="m3 16.5 9 4.5 9-4.5" />
  </Line>
);

export const PerformanceIcon = () => (
  <Line>
    <path d="M3 3v18h18" />
    <path d="m7 15 4-4 3 3 6-7" />
    <path d="M16 7h4v4" />
  </Line>
);

export const RiskIcon = () => (
  <Line>
    <path d="M12 3 4.5 6v5.5c0 4.6 3.2 8.3 7.5 9.5 4.3-1.2 7.5-4.9 7.5-9.5V6L12 3Z" />
    <path d="M12 8v4.5" />
    <path d="M12 16h.01" />
  </Line>
);

export const TradingIcon = () => (
  <Line>
    <path d="M7 4v16" />
    <path d="m3.5 7.5 3.5-3.5 3.5 3.5" />
    <path d="M17 20V4" />
    <path d="m20.5 16.5-3.5 3.5-3.5-3.5" />
  </Line>
);

export const OpsIcon = () => (
  <Line>
    <rect x="5" y="5" width="14" height="14" rx="3" />
    <rect x="9" y="9" width="6" height="6" rx="1" />
    <path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3" />
  </Line>
);

export const HaltIcon = () => (
  <Line size={15}>
    <path d="M8.2 2.5h7.6l5.7 5.7v7.6l-5.7 5.7H8.2l-5.7-5.7V8.2l5.7-5.7Z" />
    <path d="M9.5 9.5v5M14.5 9.5v5" />
  </Line>
);

export const SearchIcon = () => (
  <Line size={15}>
    <circle cx="11" cy="11" r="6.5" />
    <path d="m20 20-4.2-4.2" />
  </Line>
);

export function SunIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinecap="round"
    >
      <circle cx="8" cy="8" r="3" />
      <path d="M8 1.5v1.6M8 12.9v1.6M1.5 8h1.6M12.9 8h1.6M3.4 3.4l1.1 1.1M11.5 11.5l1.1 1.1M3.4 12.6l1.1-1.1M11.5 4.5l1.1-1.1" />
    </svg>
  );
}

export function MoonIcon() {
  return (
    <svg
      width="16"
      height="16"
      viewBox="0 0 16 16"
      aria-hidden="true"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.4"
      strokeLinejoin="round"
    >
      <path d="M13.5 9.6A5.8 5.8 0 0 1 6.4 2.5a5.8 5.8 0 1 0 7.1 7.1Z" />
    </svg>
  );
}

export function SortIcon({ dir }: { dir: "asc" | "desc" | false }) {
  return (
    <svg width="8" height="10" viewBox="0 0 8 10" aria-hidden="true">
      <path d="M4 1 L7 4 H1 Z" fill={dir === "asc" ? "currentColor" : "var(--rule-strong)"} />
      <path d="M4 9 L1 6 H7 Z" fill={dir === "desc" ? "currentColor" : "var(--rule-strong)"} />
    </svg>
  );
}
