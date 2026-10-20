import { useState } from "react";
import { Link } from "react-router";
import type { Schemas } from "../api/client";
import { useRebalancePreview, useSubmitRebalance, useTradingDay } from "../api/hooks";
import { dateLong, money } from "../lib/format";
import { LoadError, Panel } from "./bits";
import { ConfirmDialog } from "./ConfirmDialog";
import { StatusIcon } from "./icons";
import { useToast } from "./Toasts";

type Day = Schemas["TradingDay"];
type Step = Schemas["TradingDayStep"];

// a running rebalance takes the steps' half-filled "working" mark instead
const STATE_ICON: Record<Exclude<Day["state"], "running">, "ok" | "error" | "unknown"> = {
  done: "ok",
  held: "ok",
  not_yet: "unknown",
  failed: "error",
  closed: "unknown",
};

/** The states in which "Rebalance now" is offered, and the preview of what it would trade is shown. */
export const OPEN_STATES: readonly Day["state"][] = ["not_yet", "held", "failed"];

/** A step's mark: filled once done, half-filled while it runs, hollow before. */
function StepMark({ state }: { state: Step["state"] }) {
  if (state === "done") return <StatusIcon status="ok" />;
  if (state === "failed") return <StatusIcon status="error" />;
  if (state === "running")
    return (
      <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
        <circle cx="5" cy="5" r="4" fill="none" stroke="var(--accent)" strokeWidth="1.5" />
        <path d="M5 1 A4 4 0 0 1 5 9 Z" fill="var(--accent)" />
      </svg>
    );
  return (
    <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
      <circle
        cx="5"
        cy="5"
        r="4"
        fill="none"
        stroke="var(--status-unknown)"
        strokeWidth="1.5"
        strokeDasharray={state === "skipped" ? "2 2" : undefined}
      />
    </svg>
  );
}

const STEP_STATE_TEXT: Record<Step["state"], string> = {
  pending: "to do",
  running: "working",
  done: "done",
  failed: "failed",
  skipped: "skipped",
};

/**
 * "Did we trade today?" in one plain answer. On Trading it carries the steps of a rebalance
 * started from here and the one button that starts it; on the Command Center, the answer alone.
 */
export function TradingToday({ compact = false }: { compact?: boolean }) {
  const { data: day, error } = useTradingDay();
  const title = "Trading today";
  if (error)
    return (
      <Panel title={title}>
        <LoadError error={error} />
      </Panel>
    );
  return (
    <Panel
      title={title}
      meta={day ? dateLong(day.trading_date) : undefined}
      className="today-panel"
      actions={
        compact ? (
          <Link to="/trading" className="btn btn-ghost" data-control="overview.trading-day.more">
            Trading →
          </Link>
        ) : undefined
      }
    >
      {day ? (
        <>
          <div className="today" data-state={day.state} data-testid="trading-day">
            <div className="today-text">
              <p className="today-headline">
                <span className="badge-icon">
                  {day.state === "running" ? (
                    <StepMark state="running" />
                  ) : (
                    <StatusIcon status={STATE_ICON[day.state]} />
                  )}
                </span>
                {day.headline}
              </p>
              <p className="today-detail">{day.detail}</p>
              {day.note && <p className="today-note">{day.note}</p>}
            </div>
            {!compact && OPEN_STATES.includes(day.state) && <RebalanceNow day={day} />}
          </div>
          {!compact && day.steps.length > 0 && (
            <ol className="today-steps" aria-label="Rebalance steps">
              {day.steps.map((s) => (
                <li key={s.id} data-state={s.state}>
                  <span className="badge">
                    <span className="badge-icon">
                      <StepMark state={s.state} />
                    </span>
                    {s.label}
                  </span>
                  <span className="muted">
                    <span className="visually-hidden">{STEP_STATE_TEXT[s.state]}: </span>
                    {s.detail}
                  </span>
                </li>
              ))}
            </ol>
          )}
        </>
      ) : (
        <p className="today-detail" aria-busy="true">
          …
        </p>
      )}
    </Panel>
  );
}

/** The one button: it starts the daily run itself, behind the same typed confirmation as before. */
function RebalanceNow({ day }: { day: Day }) {
  const preview = useRebalancePreview().data;
  const submit = useSubmitRebalance();
  const toast = useToast();
  const [confirming, setConfirming] = useState(false);
  const failing = preview?.checks.find((c) => c.status === "fail");
  const ready = day.can_rebalance && !!preview?.can_submit;
  const why =
    day.blocked_reason ??
    (!preview
      ? "Checking…"
      : failing
        ? `${failing.label}: ${failing.detail}`
        : preview.totals.orders === 0
          ? "Nothing to trade: the book is on target."
          : `About ${preview.totals.orders} orders. Requires typed confirmation.`);
  return (
    <div className="today-action">
      <button
        type="button"
        className="btn btn-primary"
        disabled={!ready}
        onClick={() => setConfirming(true)}
        data-control="trading.today.rebalance"
      >
        Rebalance now
      </button>
      <span className="today-why">{why}</span>
      {preview && (
        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title="Rebalance now?"
          confirmLabel="Start the rebalance"
          requireText="SUBMIT"
          control="rebalance.submit"
          onConfirm={async () => {
            const result = await submit.mutateAsync();
            toast(result.message, "success");
          }}
        >
          <p>
            The engine runs today&rsquo;s pipeline now: it reconciles with IBKR, refreshes prices and targets, builds
            the basket from live positions and, if every check passes (the kill switch, the no-leverage check, the
            duplicate guard), sends about {preview.totals.orders} orders to the paper account (buy{" "}
            {money(preview.totals.buy_notional)}, sell {money(preview.totals.sell_notional)}).
          </p>
          <p>{preview.execution}.</p>
        </ConfirmDialog>
      )}
    </div>
  );
}
