import { useMarketStrip, useOrders, useRebalancePreview, useStatus } from "../api/hooks";
import { StatusIcon } from "../components/icons";
import { dateShort, datelineET, shortTimeET } from "../lib/format";
import { useServerNow } from "../lib/serverClock";

const MODE_TEXT = { demo: "Demo", paper: "Paper", live: "Live" } as const;
const MODE_NOTE = {
  demo: "Simulated $1M paper book",
  paper: "IBKR paper account",
  live: "Real money",
} as const;

/**
 * The nameplate, set like a paper's front page: DV02 in the middle, an "ear" on each
 * side (the edition and account on the left, the gateway and today's orders on the
 * right), and the dateline under a thick-and-thin rule.
 */
export function Masthead() {
  const now = useServerNow();
  const status = useStatus().data;
  const preview = useRebalancePreview().data;
  const orders = useOrders().data;
  const strip = useMarketStrip().data;

  const gateway = status?.components.find((c) => c.id === "gateway");
  const endpoint = gateway?.detail.match(/(\d{1,3}(?:\.\d{1,3}){3}:\d{2,5})/)?.[1];
  const next = status?.schedule.find((j) => j.status === "scheduled" || j.status === "running");
  const list = orders?.orders ?? [];
  const filled = list.filter((o) => o.status === "filled").length;
  const working = list.filter((o) => ["pending", "submitted", "partial"].includes(o.status)).length;
  const quotes = strip ? (strip.items.length === 0 ? "none" : strip.delayed ? "delayed 15 min" : "real-time") : "…";

  return (
    <header className="masthead">
      <div className="ear ear-left">
        {status && (
          <>
            <p className="ear-kicker">{MODE_TEXT[status.mode]} edition</p>
            <p>{MODE_NOTE[status.mode]}</p>
            <p>
              Account <span className="num">{status.account.id}</span>
            </p>
          </>
        )}
      </div>

      <div className="brand">
        <span className="wordmark">DV02</span>
      </div>

      <div className="ear ear-right">
        <p className="ear-kicker">Gateway</p>
        {gateway ? (
          <p className="side-status">
            <StatusIcon status={gateway.status} size={8} />
            <span>{gateway.status === "ok" ? "Running" : gateway.status === "error" ? "Down" : "Degraded"}</span>
            {endpoint && <span className="num muted">{endpoint}</span>}
          </p>
        ) : (
          <p className="muted">…</p>
        )}
        <p>
          Orders {orders ? dateShort(orders.trading_date) : "today"}:{" "}
          <span className="num">
            {filled} filled · {working} working
          </span>
        </p>
        <p className="ear-next">Next: {next ? `${next.label}, ${shortTimeET(next.at)} ET` : "nothing scheduled"}</p>
      </div>

      <p className="dateline">
        <time className="brand-date" dateTime={now.toISOString().slice(0, 10)}>
          {datelineET(now)}
        </time>
        <span className="dateline-extra">Latest close {preview ? dateShort(preview.targets_date) : "…"}</span>
        <span className="dateline-extra">Quotes {quotes}</span>
      </p>
    </header>
  );
}
