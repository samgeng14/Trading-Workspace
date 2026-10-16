import type { ReactNode } from "react";
import { Link } from "react-router";
import { useRebalancePreview, useStatus } from "../api/hooks";
import { useStream } from "../api/stream";
import { HaltIcon, MoonIcon, SessionIcon, StatusIcon, SunIcon } from "../components/icons";
import { useKillSwitchDialogs } from "../components/KillSwitch";
import { countdown, timeET, weekdayTimeET } from "../lib/format";
import { gate } from "../lib/readiness";
import { useServerNow } from "../lib/serverClock";
import { sessionProgress } from "../lib/session";
import { useTheme } from "../lib/useTheme";

const SESSION_LABEL = { pre: "Pre-market", open: "Market open", post: "After hours", closed: "Market closed" };
const MODE_TEXT = { demo: "Demo", paper: "Paper", live: "Live" } as const;
const HEALTH_SHORT: Record<string, string> = {
  gateway: "IBKR",
  market_data: "Data",
  engine: "Engine",
  scheduler: "Sched",
  database: "DB",
};

function Seg({ label, children, className = "" }: { label: string; children: ReactNode; className?: string }) {
  return (
    <div className={`seg ${className}`}>
      <span className="seg-label">{label}</span>
      <span className="seg-value">{children}</span>
    </div>
  );
}

/** "4s ago", "2m ago". */
function since(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  return s < 60 ? `${s}s ago` : `${Math.floor(s / 60)}m ago`;
}

/** The status line over the nameplate: what mode, is the market open, can we trade, is everything up, and the halt. */
export function TopBar() {
  const { data: status } = useStatus();
  const stream = useStream();
  const now = useServerNow();
  const [theme, toggleTheme] = useTheme();
  const killSwitch = useKillSwitchDialogs();
  const preview = useRebalancePreview().data;

  const market = status?.market;
  const untilChange = market ? Date.parse(market.next_change_at) - now.getTime() : 0;
  const changeLabel =
    market && untilChange < 18 * 3600_000
      ? `${market.next_change === "opens" ? "Opens" : "Closes"} in ${countdown(untilChange)}`
      : market
        ? `Opens ${weekdayTimeET(market.next_change_at)} ET`
        : "";
  const progress = sessionProgress(market, now);
  const g = gate(status, preview);
  const clock = timeET(now);
  const healthy = status?.components.filter((c) => c.status === "ok").length ?? 0;

  return (
    <header className="topbar">
      <div className="topbar-id">
        {status && <span className={`mode-badge mode-${status.mode}`}>{MODE_TEXT[status.mode]}</span>}
      </div>

      <div className="topbar-segs">
        {market && (
          <Seg label="Market" className="topbar-session">
            <SessionIcon session={market.session} />
            <span className="session-label">{SESSION_LABEL[market.session]}</span>
            <span className="muted">· {changeLabel}</span>
            {progress !== null && (
              <span className="seg-progress" aria-hidden="true">
                <span style={{ width: `${progress * 100}%` }} />
              </span>
            )}
          </Seg>
        )}
        <Seg label="Gate">
          <span className={`tone-${g.tone}`} title="Can the next rebalance be submitted now (pre-trade checks)">
            {g.text}
          </span>
        </Seg>
        <Seg label="Stream" className="topbar-stream">
          {stream.live ? (
            <span title="The API pushes every change over a WebSocket; REST is re-checked every 30 s as a safety net">
              Live
              {stream.lastFrameAt !== null && (
                <span className="muted"> · {since(Date.now() - stream.lastFrameAt)}</span>
              )}
            </span>
          ) : (
            <span
              className="muted"
              title={
                status?.mode === "demo"
                  ? "Demo mode has no stream; the simulated backend is polled every 5 s"
                  : "No stream from the API right now; polling every 5 s and reconnecting"
              }
            >
              Polling
            </span>
          )}
        </Seg>
        {status && (
          <Link
            to="/ops"
            className="seg topbar-health"
            data-control="topbar.health"
            aria-label="System health: open Operations"
          >
            <span className="seg-label">Systems</span>
            <span className="seg-value">
              {status.components.map((c) => (
                <span key={c.id} className="badge" title={`${c.label}: ${c.detail}`}>
                  <span className="badge-icon">
                    <StatusIcon status={c.status} />
                  </span>
                  <span className="badge-text">{HEALTH_SHORT[c.id] ?? c.label}</span>
                </span>
              ))}
              <span className="health-count">
                {healthy}/{status.components.length}
              </span>
            </span>
          </Link>
        )}
      </div>

      <div className="topbar-right">
        <span className="topbar-clock num" aria-label="Exchange time">
          <span>
            {clock.slice(0, 5)}
            <span className="clock-sec">{clock.slice(5)}</span>
          </span>
          <span className="muted">ET</span>
        </span>
        {status && !status.kill_switch.armed && (
          <button
            type="button"
            className="btn btn-danger halt-btn"
            onClick={killSwitch.openArm}
            aria-label="Halt trading"
            data-control="topbar.kill-switch"
          >
            <HaltIcon />
            Halt<span className="halt-btn-more"> trading</span>
          </button>
        )}
        <button
          type="button"
          className="btn btn-icon btn-ghost"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          data-control="topbar.theme"
        >
          {theme === "dark" ? <SunIcon /> : <MoonIcon />}
        </button>
      </div>

      {killSwitch.dialogs}
    </header>
  );
}
