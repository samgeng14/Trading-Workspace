import { useMarketStrip } from "../api/hooks";
import { direction, num, pct } from "../lib/format";

export function TickerStrip() {
  const { data } = useMarketStrip();
  return (
    <div className="strip" aria-label="Markets">
      <ul className="strip-items">
        {data?.items.map((q) => {
          const dir = direction(q.change, q.kind === "yield" ? 0.00005 : 1e-9);
          const change = q.kind === "yield" ? `${num(q.change * 100, 0, true)} bp` : pct(q.change_pct, 2, true);
          const last =
            q.kind === "yield" ? `${num(q.last, 2)}%` : num(q.last, q.last < 100 ? 2 : q.last > 20_000 ? 0 : 2);
          return (
            <li key={q.symbol} className="strip-item">
              <span className="strip-label">{q.label}</span>
              <span className="num">{last}</span>
              <span className={`num ${dir}`}>{change}</span>
            </li>
          );
        })}
      </ul>
      {data?.delayed && <span className="strip-note">Delayed 15 min</span>}
    </div>
  );
}
