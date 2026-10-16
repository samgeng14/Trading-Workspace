// Small inline icons. Status icons differ by shape as well as colour, so
// state never depends on colour alone.

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
        <path d="M5 0.6 L9.6 9.2 H0.4 Z" fill="var(--status-warning)" />
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

export function SunIcon() {
  return (
    <svg
      width="14"
      height="14"
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
      width="14"
      height="14"
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
