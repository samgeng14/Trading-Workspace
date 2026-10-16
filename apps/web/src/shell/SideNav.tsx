import { type ComponentType, useMemo } from "react";
import { NavLink, useNavigate } from "react-router";
import { CommandIcon, OpsIcon, PerformanceIcon, PositionsIcon, RiskIcon, TradingIcon } from "../components/icons";
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

/**
 * The sections: a row of capitals under the masthead, as a paper's section bar, kept
 * in view while the page scrolls. On a phone it is the tab bar along the bottom.
 */
export function SideNav() {
  const navigate = useNavigate();
  const shortcuts = useMemo(() => Object.fromEntries(NAV.map((n) => [n.key, () => navigate(n.to)])), [navigate]);
  useGoHotkeys(shortcuts);

  return (
    <nav className="sidenav" aria-label="Sections">
      <ul className="sidenav-list">
        {NAV.map((n) => (
          <li key={n.to}>
            <NavLink to={n.to} end={n.to === "/"} className="sidenav-link" data-control={n.control}>
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
            </NavLink>
          </li>
        ))}
      </ul>
    </nav>
  );
}
