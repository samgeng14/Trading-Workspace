import type { Schemas } from "../api/client";
import { limitText } from "./labels";

/**
 * "Is the book safe to act on today?" — one verdict built from what the API
 * already reports: kill switch, broker and data health, the rebalance
 * pre-trade checks, risk limits, the IBKR doctor and the last daily run.
 *
 * Each check is ok, warn (a person should look), block (a person must act
 * before trading as normal) or unknown (not reported yet). Blockers and warnings are the
 * individual reasons, so the Command Center can say exactly what is wrong.
 */

export type CheckState = "ok" | "warn" | "block" | "unknown";

export interface ReadinessCheck {
  id: string;
  label: string;
  state: CheckState;
  value: string;
  to: string;
}

export interface Issue {
  id: string;
  title: string;
  detail: string;
  to: string;
}

export interface Readiness {
  verdict: "clear" | "review" | "blocked" | "loading";
  checks: ReadinessCheck[];
  blockers: Issue[];
  warnings: Issue[];
}

export interface ReadinessInput {
  status?: Schemas["SystemStatus"];
  preview?: Schemas["RebalancePreview"];
  risk?: Schemas["RiskReport"];
  doctor?: Schemas["DoctorReport"];
  runs?: Schemas["RunsResponse"];
}

const HEALTH_STATE: Record<Schemas["Health"], CheckState> = {
  ok: "ok",
  warning: "warn",
  error: "block",
  unknown: "unknown",
};

export function readiness({ status, preview, risk, doctor, runs }: ReadinessInput): Readiness {
  const checks: ReadinessCheck[] = [];
  const blockers: Issue[] = [];
  const warnings: Issue[] = [];
  const raise = (state: CheckState, issue: Issue) => {
    if (state === "block") blockers.push(issue);
    else if (state === "warn") warnings.push(issue);
  };

  // Trading switch
  if (status) {
    const ks = status.kill_switch;
    const state: CheckState = ks.armed ? "block" : "ok";
    checks.push({
      id: "kill_switch",
      label: "Trading enabled",
      state,
      value: ks.armed ? "Halted" : "Enabled",
      to: "/ops",
    });
    raise(state, {
      id: "kill_switch",
      title: "Trading halted",
      detail: `Kill switch armed${ks.reason ? `: ${ks.reason}` : ""}`,
      to: "/ops",
    });
  } else {
    checks.push({ id: "kill_switch", label: "Trading enabled", state: "unknown", value: "…", to: "/ops" });
  }

  // Broker and data feeds
  for (const [id, label, okText] of [
    ["gateway", "Broker reachable", "Connected"],
    ["market_data", "Market data", "Live"],
  ] as const) {
    const c = status?.components.find((x) => x.id === id);
    const state = c ? HEALTH_STATE[c.status] : "unknown";
    const value = !c ? "…" : state === "ok" ? okText : shortDetail(c.detail);
    checks.push({ id, label, state, value, to: "/ops" });
    if (c) raise(state, { id, title: c.label, detail: c.detail, to: "/ops" });
  }

  // Rebalance gate
  if (preview) {
    const submitted = preview.checks.some((c) => c.id === "duplicate" && c.status === "fail");
    // The kill switch has its own row; the duplicate guard failing means orders are already in.
    const gating = preview.checks.filter((c) => c.id !== "duplicate" && c.id !== "kill_switch");
    const fails = gating.filter((c) => c.status === "fail");
    const warns = gating.filter((c) => c.status === "warn");
    const state: CheckState = submitted ? "ok" : fails.length ? "block" : warns.length ? "warn" : "ok";
    const value = submitted
      ? "Submitted"
      : fails.length
        ? `${fails.length} failing`
        : `${gating.length - warns.length}/${gating.length} pass`;
    checks.push({ id: "pretrade", label: "Pre-trade checks", state, value, to: "/trading" });
    if (!submitted) {
      for (const c of fails)
        blockers.push({ id: `pretrade.${c.id}`, title: c.label, detail: c.detail, to: "/trading" });
      for (const c of warns)
        warnings.push({ id: `pretrade.${c.id}`, title: c.label, detail: c.detail, to: "/trading" });
    }
  } else {
    checks.push({ id: "pretrade", label: "Pre-trade checks", state: "unknown", value: "…", to: "/trading" });
  }

  // Risk limits
  if (risk) {
    const breach = risk.limits.filter((l) => l.status === "breach");
    const warn = risk.limits.filter((l) => l.status === "warning");
    const state: CheckState = breach.length ? "block" : warn.length ? "warn" : "ok";
    const within = risk.limits.length - breach.length - warn.length;
    checks.push({
      id: "limits",
      label: "Risk limits",
      state,
      value: state === "ok" ? `${within}/${risk.limits.length} within` : `${breach.length + warn.length} flagged`,
      to: "/risk",
    });
    for (const l of breach) blockers.push({ id: `limit.${l.id}`, title: l.label, detail: limitDetail(l), to: "/risk" });
    for (const l of warn) warnings.push({ id: `limit.${l.id}`, title: l.label, detail: limitDetail(l), to: "/risk" });
  } else {
    checks.push({ id: "limits", label: "Risk limits", state: "unknown", value: "…", to: "/risk" });
  }

  // IBKR doctor
  if (doctor) {
    const fail = doctor.results.filter((r) => r.status === "FAIL");
    const warn = doctor.results.filter((r) => r.status === "WARN");
    const pass = doctor.results.filter((r) => r.status === "PASS").length;
    const state: CheckState = fail.length ? "block" : warn.length ? "warn" : "ok";
    checks.push({
      id: "doctor",
      label: "IBKR doctor",
      state,
      value: [`${pass} pass`, fail.length && `${fail.length} fail`, warn.length && `${warn.length} warn`]
        .filter(Boolean)
        .join(" · "),
      to: "/ops",
    });
    for (const r of fail)
      blockers.push({ id: `doctor.${r.name}`, title: `Doctor: ${r.name}`, detail: r.fix || r.detail, to: "/ops" });
    for (const r of warn)
      warnings.push({ id: `doctor.${r.name}`, title: `Doctor: ${r.name}`, detail: r.fix || r.detail, to: "/ops" });
  } else {
    checks.push({ id: "doctor", label: "IBKR doctor", state: "unknown", value: "…", to: "/ops" });
  }

  // Last daily run
  const daily = runs?.runs.find((r) => r.kind === "daily");
  if (daily) {
    const state: CheckState = daily.ok ? "ok" : "warn";
    checks.push({
      id: "daily_run",
      label: "Last daily run",
      state,
      value: daily.ok ? (daily.dry_run ? "OK · dry run" : "OK") : "Failed",
      to: "/ops",
    });
    raise(state, { id: "daily_run", title: "Daily run failed", detail: daily.summary, to: "/ops" });
  } else {
    checks.push({
      id: "daily_run",
      label: "Last daily run",
      state: "unknown",
      value: runs ? "None yet" : "…",
      to: "/ops",
    });
  }

  const loading = !status || !preview || !risk;
  const verdict = blockers.length ? "blocked" : loading ? "loading" : warnings.length ? "review" : "clear";
  return { verdict, checks, blockers, warnings };
}

/** "13.2% vs limit 12.0% · 63-day, annualized": the numbers first, then the limit's own note. */
function limitDetail(l: Schemas["LimitCheck"]): string {
  const vs = `${limitText(l.value, l.unit)} vs limit ${limitText(l.limit, l.unit)}`;
  return l.detail ? `${vs} · ${l.detail}` : vs;
}

/** First clause of a component detail, e.g. "Delayed 15 min: no live…" → "Delayed 15 min". */
function shortDetail(detail: string): string {
  const head = detail.split(/[:;(]/)[0]!.trim();
  return head.length > 28 ? `${head.slice(0, 27)}…` : head;
}

/**
 * The engine's own pre-trade gate, as opposed to the verdict above: can the
 * next rebalance be submitted right now? A risk breach makes the verdict
 * "action required" but does not by itself stop orders; this says whether
 * they would go.
 */
export function gate(
  status: Schemas["SystemStatus"] | undefined,
  preview: Schemas["RebalancePreview"] | undefined,
): { text: string; tone: "good" | "bad" | "accent" | "neutral" } {
  if (!status || !preview) return { text: "…", tone: "neutral" };
  if (status.kill_switch.armed) return { text: "Halted", tone: "bad" };
  if (preview.checks.some((c) => c.id === "duplicate" && c.status === "fail"))
    return { text: "Submitted", tone: "accent" };
  return preview.can_submit ? { text: "Ready", tone: "good" } : { text: "Blocked", tone: "bad" };
}

export const VERDICT_TEXT: Record<Readiness["verdict"], string> = {
  clear: "All clear",
  review: "Review",
  blocked: "Action required",
  loading: "Checking",
};
