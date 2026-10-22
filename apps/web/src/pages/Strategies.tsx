import type { Schemas } from "../api/client";
import { useStrategies } from "../api/hooks";
import { LineChart, monthTicks } from "../charts/LineChart";
import { tickDecimals } from "../charts/axis";
import { Figure } from "../components/Figure";
import { LoadError, PageHead, Panel, StatusBadge } from "../components/bits";
import { dateLong, dateShort, money, num, pct } from "../lib/format";

const STATUS = { live: "ok", paper: "ok", shadow: "warning", research: "unknown", failed: "error" } as const;
const LABEL = {
  live: "Live",
  paper: "Paper",
  shadow: "Shadow",
  research: "In research",
  failed: "Failed the gate",
} as const;

type ShadowBook = Schemas["ShadowBook"];

export function Strategies() {
  const { data, error } = useStrategies();
  return (
    <div className="page">
      <PageHead
        title="Strategies"
        sub="Each sleeve against the research gate, and each shadow book beside the live fund"
      />
      <Panel
        title="Sleeves"
        meta="walk-forward to 2023, costs on, Sharpe over T-bills"
        flush
        foot={
          <>
            A sleeve trades only after passing the research gate and three months of shadow (docs/PLAN.md §9). The
            studies are under <code>trading-engine/research</code>; the list is <code>config/sleeves.yaml</code>.
          </>
        }
      >
        {error ? (
          <LoadError error={error} />
        ) : (
          <div className="table-wrap">
            <table className="table sleeves">
              <thead>
                <tr>
                  <th>Sleeve</th>
                  <th>Status</th>
                  <th>Since</th>
                  <th className="r">Sharpe</th>
                  <th className="r">Max drawdown</th>
                </tr>
              </thead>
              <tbody>
                {data?.sleeves.map((s) => (
                  <tr key={s.id} data-status={s.status}>
                    <td className="sleeve-name">
                      <span>{s.name}</span>
                      <span className="sleeve-summary">{s.summary}</span>
                    </td>
                    <td>
                      <StatusBadge status={STATUS[s.status]} label={LABEL[s.status]} />
                    </td>
                    <td className="num">{s.since ? dateShort(s.since) : "—"}</td>
                    <td className="num r">
                      {s.sharpe === null ? "—" : num(s.sharpe, 2)}
                      {s.sharpe_se !== null && <span className="secondary"> ± {num(s.sharpe_se, 2)}</span>}
                    </td>
                    <td className="num r">{s.max_drawdown === null ? "—" : pct(s.max_drawdown, 1)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
      {(data?.shadows ?? []).map((book) => (
        <ShadowSection key={book.id} book={book} />
      ))}
    </div>
  );
}

/** One shadow book: its record beside the live fund, and what it would do now. */
function ShadowSection({ book }: { book: ShadowBook }) {
  const points = book.points;
  const dates = points.map((p) => p.date);
  const growth = (v: number) => (v - 1) * 100;
  const weights = Object.entries(book.decision?.weights ?? {}).sort((a, b) => b[1] - a[1]);
  const tag = book.id.toUpperCase();
  const futures = book.holdings.length > 0;
  return (
    <section className="shadow-book" aria-label={`Shadow book ${tag}`}>
      <Figure
        title={book.name}
        meta={
          points.length
            ? `shadow book · since ${dateShort(book.start)} · ${book.sessions} sessions`
            : `shadow book · from ${dateShort(book.start)}`
        }
        control={`strategies.shadow.${book.id}`}
        chart={
          points.length ? (
            <LineChart
              series={[
                {
                  id: "book",
                  label: "Shadow book",
                  color: "var(--series-1)",
                  values: points.map((p) => growth(p.book)),
                },
                { id: "live", label: "Live S0", color: "var(--series-2)", values: points.map((p) => growth(p.live)) },
                {
                  id: "sleeve",
                  label: book.sleeve,
                  color: "var(--bench)", // a component of the book, drawn like a benchmark
                  values: points.map((p) => growth(p.sleeve)),
                },
              ]}
              baseline={0}
              yFormat={(v, ticks) => `${num(v, tickDecimals(ticks), true)}%`}
              xLabel={(i) => dateLong(dates[i]!)}
              xTicks={monthTicks(dates)}
              height={220}
              ariaLabel={`${tag}'s shadow book: its return since its start, beside the live fund's and the sleeve's`}
              control={`strategies.shadow.${book.id}.plot`}
            />
          ) : (
            <div className="history-empty" role="status">
              <p className="history-empty-title">This shadow book has no record yet.</p>
              <p className="history-empty-body">
                It starts on {dateLong(book.start)}, and its first numbers come once the prices run past that day, the
                evening after. It runs at 17:05 ET each weekday and sends nothing.
              </p>
            </div>
          )
        }
        table={{
          head: ["Date", "Shadow book", "Live S0", book.sleeve],
          numeric: [1, 2, 3],
          rows: points.map((p) => [
            p.date,
            pct(p.book - 1, 2, true),
            pct(p.live - 1, 2, true),
            pct(p.sleeve - 1, 2, true),
          ]),
        }}
      />

      <Panel
        title={`What ${tag} would do`}
        meta={book.through ? `as of ${dateShort(book.through)}` : undefined}
        flush
        foot={
          futures ? (
            <>
              Nothing here is sent: the short the sleeve would hold for the next session, at the live account&rsquo;s
              NAV. The runner can&rsquo;t trade VIX futures yet.
            </>
          ) : (
            <>Nothing here is sent: these are the orders that would take the account from S0 to the shadow book.</>
          )
        }
      >
        {book.signal && <p className="secondary panel-note shadow-signal">{book.signal}</p>}
        {futures ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Future</th>
                  <th className="r">Contracts</th>
                  <th className="r">Notional</th>
                </tr>
              </thead>
              <tbody>
                {book.holdings.map((h) => (
                  <tr key={h.instrument}>
                    <td>
                      <span className="sym">{h.instrument}</span>
                    </td>
                    <td className="num r">{num(h.quantity)}</td>
                    <td className="num r">{money(h.notional)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : weights.length || book.would_be_orders.length ? (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Order</th>
                  <th className="r">Shares</th>
                  <th className="r">Value</th>
                </tr>
              </thead>
              <tbody>
                {book.would_be_orders.slice(0, 12).map((o) => (
                  <tr key={o.symbol}>
                    <td>
                      {o.action === "BUY" ? "Buy" : "Sell"} <span className="sym">{o.symbol}</span>
                    </td>
                    <td className="num r">{num(o.quantity)}</td>
                    <td className="num r">{money(o.dollars)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {weights.length > 0 && (
              <p className="secondary sleeve-weights">
                {book.sleeve}, decided {dateShort(book.decision!.decided)}:{" "}
                {weights.map(([s, w]) => `${s} ${pct(w, 0)}`).join(" · ")}
              </p>
            )}
          </div>
        ) : (
          !book.signal && (
            <p className="secondary panel-note">Nothing yet: the first decision comes with the first record.</p>
          )
        )}
      </Panel>
    </section>
  );
}
