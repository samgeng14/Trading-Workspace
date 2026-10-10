import { Link } from "react-router";
import { useStatus } from "../api/hooks";
import { MoonIcon, SessionIcon, StatusIcon, SunIcon } from "../components/icons";
import { useKillSwitchDialogs } from "../components/KillSwitch";
import { countdown, timeET, weekdayTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";
import { useTheme } from "../lib/useTheme";

const SESSION_LABEL = { pre: "Pre-market", open: "Market open", post: "After hours", closed: "Market closed" };
const HEALTH_SHORT: Record<string, string> = {
  gateway: "IBKR",
  market_data: "Data",
  engine: "Engine",
  scheduler: "Scheduler",
  database: "DB",
};

export function TopBar() {
  const { data: status } = useStatus();
  const now = useServerNow();
  const [theme, toggleTheme] = useTheme();
  const killSwitch = useKillSwitchDialogs();

  const market = status?.market;
  const untilChange = market ? Date.parse(market.next_change_at) - now.getTime() : 0;
  const changeLabel =
    market && untilChange < 18 * 3600_000
      ? `${market.next_change === "opens" ? "Opens" : "Closes"} in ${countdown(untilChange)}`
      : market
        ? `Opens ${weekdayTimeET(market.next_change_at)} ET`
        : "";

  return (
    <header className="topbar">
      <div className="topbar-brand">
        <span className="wordmark">DV02 Fund</span>
        {status && (
          <span className={`mode-badge mode-${status.mode}`}>{status.mode === "demo" ? "Demo data" : status.mode}</span>
        )}
        {status && <span className="topbar-account num">{status.account.id}</span>}
      </div>

      {market && (
        <div className="topbar-session" aria-live="polite">
          <SessionIcon session={market.session} />
          <span className="session-label">{SESSION_LABEL[market.session]}</span>
          <span className="muted num">{changeLabel}</span>
        </div>
      )}

      <div className="topbar-right">
        {status && (
          <Link
            to="/ops"
            className="topbar-health"
            data-control="topbar.health"
            aria-label="System health: open Operations"
          >
            {status.components.map((c) => (
              <span key={c.id} className="badge" title={`${c.label}: ${c.detail}`}>
                <span className="badge-icon">
                  <StatusIcon status={c.status} />
                </span>
                {HEALTH_SHORT[c.id] ?? c.label}
              </span>
            ))}
          </Link>
        )}
        <span className="topbar-clock num" aria-label="Exchange time">
          {timeET(now)} <span className="muted">ET</span>
        </span>
        {status && !status.kill_switch.armed && (
          <button
            type="button"
            className="btn btn-danger"
            onClick={killSwitch.openArm}
            data-control="topbar.kill-switch"
          >
            Halt trading
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
