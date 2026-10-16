import { motion } from "motion/react";
import type { ReactNode } from "react";
import { direction, money, pct } from "../lib/format";
import { StatusIcon } from "./icons";

/** A signed value coloured by direction. The sign is always printed, so colour is never the only cue. */
export function Delta({
  value,
  kind = "pct",
  decimals,
  className = "",
  chip = false,
}: {
  value: number | null | undefined;
  kind?: "pct" | "money";
  decimals?: number;
  className?: string;
  /** Set on a tinted pill (headline changes). */
  chip?: boolean;
}) {
  const text =
    kind === "money" ? money(value, { sign: true, decimals: decimals ?? 0 }) : pct(value, decimals ?? 2, true);
  const dir = direction(value, kind === "money" ? 0.5 : 5e-7);
  return <span className={`num ${dir} ${chip ? "chip-delta" : ""} ${className}`}>{text}</span>;
}

export function Stat({
  label,
  value,
  sub,
  testId,
  className = "",
  visual,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  testId?: string;
  /** "tile" sets the stat on its own small card. */
  className?: string;
  /** A small picture beside the figures (a gauge). */
  visual?: ReactNode;
}) {
  const body = (
    <>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {sub !== undefined && <span className="stat-sub">{sub}</span>}
    </>
  );
  return (
    <div className={`stat ${className} ${visual ? "has-visual" : ""}`} data-testid={testId}>
      {visual ? (
        <>
          <div className="stat-text">{body}</div>
          {visual}
        </>
      ) : (
        body
      )}
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
  floor = limit === 0,
}: {
  label: string;
  value: number;
  limit: number;
  status: "ok" | "warning" | "breach";
  valueText: string;
  limitText: string;
  detail?: string;
  /**
   * The value must stay at or above the limit (cash >= 0). A zero limit is always a floor:
   * there is no share of zero to use up. A floor above zero has to be named here.
   */
  floor?: boolean;
}) {
  // How much of the limit is used. Same sign as the limit counts toward it (a -4% drawdown uses
  // 80% of a -5% limit). A floor is used from the top down: cash at 0.4% of NAV has used 99.6% of
  // the room above a 0% floor. A breach always fills the bar, so a margin loan never shows empty.
  const share = floor ? (1 - value) / (1 - limit) : value / (limit || 1);
  const used = status === "breach" ? 1 : Math.min(1, Math.max(0, share));
  return (
    <div className="meter-row" data-status={status}>
      <span className="meter-label">{label}</span>
      <span className="meter-value num">
        {valueText}
        <span className="meter-limit"> / {limitText}</span>
      </span>
      <div
        className="meter-track"
        role="meter"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(used * 100)}
        aria-valuetext={`${valueText}, limit ${limitText}`}
      >
        <div className="meter-fill" data-status={status} style={{ width: `${used * 100}%` }} />
      </div>
      <span className="meter-detail">
        <span>
          {status !== "ok" ? <StatusBadge status={status} /> : null} {detail}
        </span>
        <span className="num meter-used">{Math.round(used * 100)}% used</span>
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

export function PageHead({
  title,
  eyebrow,
  sub,
  children,
}: {
  title: string;
  /** The small accent line over the title: what part of the desk this is. */
  eyebrow?: string;
  sub?: ReactNode;
  children?: ReactNode;
}) {
  return (
    <div className="page-head">
      <div className="page-head-text">
        {eyebrow && <p className="page-eyebrow">{eyebrow}</p>}
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {children && <div className="filters">{children}</div>}
    </div>
  );
}

/** Buttons for one choice; a pill slides to the pressed one. */
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
          {value === o.value && (
            <motion.span
              layoutId={`seg-${control}`}
              className="segmented-thumb"
              transition={{ type: "spring", stiffness: 520, damping: 38 }}
            />
          )}
          <span className="segmented-text">{o.label}</span>
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
