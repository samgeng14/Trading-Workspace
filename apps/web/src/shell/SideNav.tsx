import { motion } from "motion/react";
import { type ComponentType, useMemo } from "react";
import { NavLink, useNavigate } from "react-router";
import { useMarketStrip, useOrders, useRebalancePreview, useStatus } from "../api/hooks";
import {
  BrandGlyph,
  CommandIcon,
  OpsIcon,
  PerformanceIcon,
  PositionsIcon,
  RiskIcon,
  StatusIcon,
  TradingIcon,
} from "../components/icons";
import { dateShort, datelineET, shortTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";
import { useGoHotkeys } from "../lib/useHotkeys";

export const NAV: readonly {
  to: string;
  label: string;
  /** The phone tab bar's label. */
  short: string;
  key: string;
  control: string;
  icon: ComponentType;
}[] = [
  { to: "/", label: "Command Center", short: "Command", key: "c", control: "nav.overview", icon: CommandIcon },
  { to: "/positions", label: "Positions", short: "Book", key: "p", control: "nav.positions", icon: PositionsIcon },
  {
    to: "/performance",
    label: "Performance",
    short: "Returns",
    key: "f",
    control: "nav.performance",
    icon: PerformanceIcon,
  },
  { to: "/risk", label: "Risk", short: "Risk", key: "r", control: "nav.risk", icon: RiskIcon },
  { to: "/trading", label: "Trading", short: "Trade", key: "t", control: "nav.trading", icon: TradingIcon },
  { to: "/ops", label: "Operations", short: "Ops", key: "s", control: "nav.operations", icon: OpsIcon },
];

const MODE_TEXT = { demo: "Demo", paper: "Paper", live: "Live" } as const;
const MODE_NOTE = {
  demo: "Simulated $1M paper book",
  paper: "IBKR paper account",
  live: "Real money",
} as const;

/** The rail on wider screens; on a phone its section list becomes the tab bar at the bottom. */
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
        <span className="brand-mark">
          <BrandGlyph />
        </span>
        <span className="brand-text">
          <span className="wordmark">DV02</span>
          <time className="brand-sub" dateTime={now.toISOString().slice(0, 10)}>
            {datelineET(now)}
          </time>
        </span>
      </div>

      {status && (
        <div className="rail-card">
          <div className="rail-mode">
            <span className="rail-mode-badge" data-mode={status.mode}>
              {MODE_TEXT[status.mode]}
            </span>
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
              {({ isActive }) => (
                <>
                  {isActive && (
                    <motion.span
                      layoutId="nav-pill"
                      className="nav-pill"
                      transition={{ type: "spring", stiffness: 480, damping: 38 }}
                    />
                  )}
                  <span className="nav-icon">
                    <n.icon />
                  </span>
                  <span className="nav-label">{n.label}</span>
                  <span className="nav-short" aria-hidden="true">
                    {n.short}
                  </span>
                  <kbd className="sidenav-key" aria-label={`Shortcut g then ${n.key}`}>
                    g {n.key}
                  </kbd>
                </>
              )}
            </NavLink>
          </li>
        ))}
      </ul>

      <div className="side-facts">
        <div className="rail-card">
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

        <div className="rail-card">
          <p className="side-label">Gateway</p>
          {gateway ? (
            <p className="side-status">
              <StatusIcon status={gateway.status} size={8} />
              <span>{gateway.status === "ok" ? "Running" : gateway.status === "error" ? "Down" : "Degraded"}</span>
              {endpoint && <span className="num muted">{endpoint}</span>}
            </p>
          ) : (
            <p className="side-status muted">…</p>
          )}
        </div>

        <div className="rail-card">
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
