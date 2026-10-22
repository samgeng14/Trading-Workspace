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

export function Strategies() {
  const { data, error } = useStrategies();
  const shadow = data?.shadow;
  const points = shadow?.points ?? [];
  const dates = points.map((p) => p.date);
  const growth = (v: number) => (v - 1) * 100;
  const weights = Object.entries(shadow?.decision?.weights ?? {}).sort((a, b) => b[1] - a[1]);
  return (
    <div className="page">
      <PageHead
        title="Strategies"
        sub="Each sleeve against the research gate, and the shadow book beside the live fund"
      />
      <Panel
        title="Sleeves"
        meta="walk-forward 2008–2023, costs on, Sharpe over T-bills"
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

      <Figure
        title="Shadow book"
        meta={
          shadow
            ? points.length
              ? `${shadow.name} · since ${dateShort(shadow.start)} · ${shadow.sessions} sessions`
              : `${shadow.name} · from ${dateShort(shadow.start)}`
            : undefined
        }
        control="strategies.shadow"
        chart={
          points.length ? (
            <LineChart
              series={[
                { id: "h2", label: "Shadow book", color: "var(--series-1)", values: points.map((p) => growth(p.h2)) },
                { id: "live", label: "Live S0", color: "var(--series-2)", values: points.map((p) => growth(p.live)) },
                {
                  id: "sleeve",
                  label: "Trend sleeve",
                  color: "var(--bench)", // a component of the book, drawn like a benchmark
                  values: points.map((p) => growth(p.sleeve)),
                },
              ]}
              baseline={0}
              yFormat={(v, ticks) => `${num(v, tickDecimals(ticks), true)}%`}
              xLabel={(i) => dateLong(dates[i]!)}
              xTicks={monthTicks(dates)}
              height={220}
              ariaLabel="The shadow book's return since its start, beside the live fund's and the trend sleeve's"
              control="strategies.shadow.plot"
            />
          ) : (
            <div className="history-empty" role="status">
              <p className="history-empty-title">The shadow book has no record yet.</p>
              <p className="history-empty-body">
                It starts on {shadow ? dateLong(shadow.start) : "its start date"}, and its first numbers come once the
                prices run past that day, the evening after. It runs at 17:05 ET each weekday and sends nothing.
              </p>
            </div>
          )
        }
        table={{
          head: ["Date", "Shadow book", "Live S0", "Trend sleeve"],
          numeric: [1, 2, 3],
          rows: points.map((p) => [
            p.date,
            pct(p.h2 - 1, 2, true),
            pct(p.live - 1, 2, true),
            pct(p.sleeve - 1, 2, true),
          ]),
        }}
      />

      <Panel
        title="What the shadow book would do"
        meta={shadow?.through ? `as of ${dateShort(shadow.through)}` : undefined}
        flush
        foot={<>Nothing here is sent: these are the orders that would take the account from S0 to the shadow book.</>}
      >
        {weights.length || shadow?.would_be_orders.length ? (
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
                {shadow?.would_be_orders.slice(0, 12).map((o) => (
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
                Trend sleeve, decided {dateShort(shadow!.decision!.decided)}:{" "}
                {weights.map(([s, w]) => `${s} ${pct(w, 0)}`).join(" · ")}
              </p>
            )}
          </div>
        ) : (
          <p className="secondary panel-note">Nothing yet: the first decision comes with the first record.</p>
        )}
      </Panel>
    </div>
  );
}
