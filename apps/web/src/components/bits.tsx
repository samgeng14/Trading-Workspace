import type { ReactNode } from "react";
import { direction, money, pct } from "../lib/format";
import { StatusIcon } from "./icons";

/** A signed value coloured by direction. The sign is always printed, so colour is never the only cue. */
export function Delta({
  value,
  kind = "pct",
  decimals,
  className = "",
}: {
  value: number | null | undefined;
  kind?: "pct" | "money";
  decimals?: number;
  className?: string;
}) {
  const text =
    kind === "money" ? money(value, { sign: true, decimals: decimals ?? 0 }) : pct(value, decimals ?? 2, true);
  return <span className={`num ${direction(value, kind === "money" ? 0.5 : 5e-7)} ${className}`}>{text}</span>;
}

export function Stat({
  label,
  value,
  sub,
  testId,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  testId?: string;
}) {
  return (
    <div className="stat" data-testid={testId}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub !== undefined && <span className="stat-sub">{sub}</span>}
    </div>
  );
}

type AnyStatus = "ok" | "warning" | "error" | "unknown" | "breach" | "pass" | "warn" | "fail" | "skip";
const STATUS_TEXT: Record<AnyStatus, string> = {
  ok: "OK",
  warning: "Warning",
  error: "Error",
  unknown: "Unknown",
  breach: "Breach",
  pass: "Pass",
  warn: "Warn",
  fail: "Fail",
  skip: "Skipped",
};

export function StatusBadge({ status, label }: { status: AnyStatus; label?: string }) {
  return (
    <span className="badge">
      <span className="badge-icon">
        <StatusIcon status={status} />
      </span>
      {label ?? STATUS_TEXT[status]}
    </span>
  );
}

export function Meter({
  label,
  value,
  limit,
  status,
  valueText,
  limitText,
  detail,
}: {
  label: string;
  value: number;
  limit: number;
  status: "ok" | "warning" | "breach";
  valueText: string;
  limitText: string;
  detail?: string;
}) {
  // Same sign as the limit counts toward it (a -4% drawdown uses 80% of a -5% limit).
  const used = Math.min(1, Math.max(0, value / (limit || 1)));
  return (
    <div className="meter-row" data-status={status}>
      <span className="meter-label">{label}</span>
      <span className="meter-value num">{valueText}</span>
      <div
        className="meter-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(used * 100)}
      >
        <div className="meter-fill" data-status={status} style={{ width: `${used * 100}%` }} />
      </div>
      <span className="meter-detail">
        <span>
          {status !== "ok" ? <StatusBadge status={status} /> : null} {detail}
        </span>
        <span className="num">limit {limitText}</span>
      </span>
    </div>
  );
}

export function Panel({
  title,
  meta,
  actions,
  children,
  className = "",
  flush = false,
  foot,
}: {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  flush?: boolean;
  foot?: ReactNode;
}) {
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
        {meta && <span className="panel-meta">{meta}</span>}
        {actions && <div className="panel-actions">{actions}</div>}
      </header>
      <div className={`panel-body ${flush ? "flush" : ""}`}>{children}</div>
      {foot && <footer className="panel-foot">{foot}</footer>}
    </section>
  );
}

export function PageHead({ title, sub, children }: { title: string; sub?: ReactNode; children?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="filters">{children}</div>}
    </div>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
  control,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (v: T) => void;
  label: string;
  control: string;
}) {
  return (
    <div className="segmented" role="group" aria-label={label}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
          data-control={`${control}.${o.value}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export function LoadError({ error }: { error: unknown }) {
  return (
    <p className="empty" role="alert">
      Couldn&rsquo;t load this view: {error instanceof Error ? error.message : String(error)}
    </p>
  );
}
