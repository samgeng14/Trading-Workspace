import { useMemo } from "react";
import type { Schemas } from "../api/client";
import { useMarketStrip, usePositions } from "../api/hooks";
import { direction, num, pct, price } from "../lib/format";

const ARROW = { up: "▲", down: "▼", flat: "■" } as const;
const LEAD = 4; // headline markets shown before the book's movers

function QuoteItem({ q }: { q: Schemas["Quote"] }) {
  const dir = direction(q.change, q.kind === "yield" ? 0.00005 : 1e-9);
  const change = q.kind === "yield" ? `${num(q.change * 100, 0, true)} bp` : pct(q.change_pct, 2, true);
  const last = q.kind === "yield" ? `${num(q.last, 2)}%` : num(q.last, q.last < 100 ? 2 : q.last > 20_000 ? 0 : 2);
  return (
    <li className="ticker-item">
      <span className="ticker-sym">{q.label}</span>
      <span className="num">{last}</span>
      <span className={`num ${dir}`}>
        {ARROW[dir]} {change}
      </span>
    </li>
  );
}

/** Bottom ticker: headline markets, the book's biggest gainers and losers today, then the other markets. */
export function TickerStrip() {
  const { data } = useMarketStrip();
  const positions = usePositions().data?.positions;
  const movers = useMemo(() => {
    const byDay = [...(positions ?? [])].sort((a, b) => b.day_return - a.day_return);
    const gainers = byDay.filter((p) => p.day_return > 0).slice(0, 3);
    const losers = byDay
      .filter((p) => p.day_return < 0)
      .slice(-3)
      .reverse();
    return [...gainers.map((p) => ({ tag: "Gainer", p })), ...losers.map((p) => ({ tag: "Loser", p }))];
  }, [positions]);
  const items = data?.items ?? [];

  return (
    <footer className="ticker" aria-label="Markets and book movers">
      <ul className="ticker-items">
        {items.slice(0, LEAD).map((q) => (
          <QuoteItem key={q.symbol} q={q} />
        ))}
        {movers.length > 0 && <li className="ticker-sep" aria-hidden="true" />}
        {movers.map(({ tag, p }) => {
          const dir = direction(p.day_return, 5e-7);
          return (
            <li key={p.symbol} className="ticker-item">
              <span className="ticker-tag">{tag}</span>
              <span className="ticker-sym">{p.symbol}</span>
              <span className="num">{price(p.last)}</span>
              <span className={`num ${dir}`}>
                {ARROW[dir]} {pct(p.day_return, 2, true)}
              </span>
            </li>
          );
        })}
        {items.length > LEAD && <li className="ticker-sep" aria-hidden="true" />}
        {items.slice(LEAD).map((q) => (
          <QuoteItem key={q.symbol} q={q} />
        ))}
      </ul>
      {data?.delayed && <span className="ticker-note">Delayed 15 min</span>}
    </footer>
  );
}
