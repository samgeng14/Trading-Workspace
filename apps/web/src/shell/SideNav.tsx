import { useMemo } from "react";
import { NavLink, useNavigate } from "react-router";
import { useStatus } from "../api/hooks";
import { useGoHotkeys } from "../lib/useHotkeys";

export const NAV = [
  { to: "/", label: "Overview", key: "o", control: "nav.overview" },
  { to: "/positions", label: "Positions", key: "p", control: "nav.positions" },
  { to: "/performance", label: "Performance", key: "f", control: "nav.performance" },
  { to: "/risk", label: "Risk", key: "r", control: "nav.risk" },
  { to: "/trading", label: "Trading", key: "t", control: "nav.trading" },
  { to: "/ops", label: "Operations", key: "s", control: "nav.operations" },
] as const;

export function SideNav() {
  const navigate = useNavigate();
  const mode = useStatus().data?.mode;
  const shortcuts = useMemo(() => Object.fromEntries(NAV.map((n) => [n.key, () => navigate(n.to)])), [navigate]);
  useGoHotkeys(shortcuts);

  return (
    <nav className="sidenav" aria-label="Sections">
      <ul>
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
      {mode === "demo" && (
        <div className="sidenav-note">
          <p className="kicker">Demo data</p>
          <p>Simulated $1M paper book. Not connected to IBKR.</p>
          <p>
            Real data: run the API and <code>npm run dev:api</code>.
          </p>
        </div>
      )}
    </nav>
  );
}
