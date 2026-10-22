import { useMemo } from "react";
import { NavLink, useNavigate } from "react-router";
import { useMarketStrip, useOrders, useRebalancePreview, useStatus } from "../api/hooks";
import { StatusIcon } from "../components/icons";
import { dateShort, datelineET, shortTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";
import { useGoHotkeys } from "../lib/useHotkeys";

export const NAV = [
  { to: "/", label: "Command Center", key: "c", control: "nav.overview" },
  { to: "/positions", label: "Positions", key: "p", control: "nav.positions" },
  { to: "/performance", label: "Performance", key: "f", control: "nav.performance" },
  { to: "/risk", label: "Risk", key: "r", control: "nav.risk" },
  { to: "/trading", label: "Trading", key: "t", control: "nav.trading" },
  { to: "/strategies", label: "Strategies", key: "y", control: "nav.strategies" },
  { to: "/data", label: "Data", key: "d", control: "nav.data" },
  { to: "/ops", label: "Operations", key: "s", control: "nav.operations" },
] as const;

const MODE_TEXT = { demo: "Demo", paper: "Paper", live: "Live" } as const;
const MODE_NOTE = {
  demo: "Simulated $1M paper book",
  paper: "IBKR paper account",
  live: "Real money",
} as const;

export function SideNav() {
  const navigate = useNavigate();
  const now = useServerNow();
  const status = useStatus().data;
  const preview = useRebalancePreview().data;
  const orders = useOrders().data;
  const strip = useMarketStrip().data;
  const shortcuts = useMemo(() => Object.fromEntries(NAV.map((n) => [n.key, () => navigate(n.to)])), [navigate]);
  useGoHotkeys(shortcuts);

  const gateway = status?.components.find((c) => c.id === "gateway");
  const endpoint = gateway?.detail.match(/(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})/)?.[1];
  const next = status?.schedule.find((j) => j.status === "scheduled" || j.status === "running");
  const list = orders?.orders ?? [];
  const filled = list.filter((o) => o.status === "filled").length;
  const working = list.filter((o) => ["pending", "submitted", "partial"].includes(o.status)).length;

  return (
    <nav className="sidenav" aria-label="Sections">
      <div className="brand">
        <span className="wordmark">DV02</span>
        <span className="brand-sub">
          <span className="brand-name">Dashboard</span>
          <time className="brand-date" dateTime={now.toISOString().slice(0, 10)}>
            {datelineET(now)}
          </time>
        </span>
      </div>

      {status && (
        <div className="side-section">
          <p className="side-label">Mode</p>
          <div className="side-mode">
            <span className={`mode-badge mode-${status.mode}`}>{MODE_TEXT[status.mode]}</span>
            <span className="side-note">{MODE_NOTE[status.mode]}</span>
          </div>
          <dl className="side-dl">
            <dt>Account</dt>
            <dd className="num">{status.account.id}</dd>
          </dl>
        </div>
      )}

      <ul className="sidenav-list">
        {NAV.map((n) => (
          <li key={n.to}>
            <NavLink to={n.to} end={n.to === "/"} className="sidenav-link" data-control={n.control}>
              <span>{n.label}</span>
              <kbd className="sidenav-key" aria-label={`Shortcut g then ${n.key}`}>
                g {n.key}
              </kbd>
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="side-facts">
        <div className="side-section">
          <p className="side-label">Data</p>
          <dl className="side-dl">
            <dt>Latest close</dt>
            <dd className="num">{preview ? preview.targets_date : "…"}</dd>
            <dt>Quotes</dt>
            <dd>
              {strip ? (strip.items.length === 0 ? "None" : strip.delayed ? "Delayed 15 min" : "Real-time") : "…"}
            </dd>
          </dl>
        </div>

        <div className="side-section">
          <p className="side-label">Gateway</p>
          {gateway ? (
            <p className="side-status">
              <StatusIcon status={gateway.status} size={8} />
              <span>{gateway.status === "ok" ? "Running" : gateway.status === "error" ? "Down" : "Degraded"}</span>
              {endpoint && <span className="num muted">· {endpoint}</span>}
            </p>
          ) : (
            <p className="side-status muted">…</p>
          )}
        </div>

        <div className="side-section">
          <p className="side-label">Orders</p>
          <dl className="side-dl">
            <dt>{orders ? dateShort(orders.trading_date) : "Today"}</dt>
            <dd className="num">
              {filled} filled · {working} working
            </dd>
            <dt>Next</dt>
            <dd>{next ? `${next.label}, ${shortTimeET(next.at)}` : "Nothing scheduled"}</dd>
          </dl>
        </div>
      </div>
    </nav>
  );
}
