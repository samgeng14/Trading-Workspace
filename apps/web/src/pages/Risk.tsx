import { useMemo } from "react";
import { useRisk } from "../api/hooks";
import { DivergingBars } from "../charts/DivergingBars";
import { SignedBars } from "../charts/SignedBars";
import { Delta, LoadError, Meter, PageHead, Panel, Stat } from "../components/bits";
import { Figure } from "../components/Figure";
import { money, num, pct, shortTimeET } from "../lib/format";
import { ASSET_CLASS_LABEL, limitText } from "../lib/labels";

export function Risk() {
  const { data, error } = useRisk();

  const exposureRows = useMemo(
    () =>
      (data?.by_asset_class ?? [])
        .map((r) => ({
          id: r.asset_class,
          label: ASSET_CLASS_LABEL[r.asset_class],
          long: r.long,
          short: r.short,
          net: r.net,
        }))
        .sort((a, b) => b.long - b.short - (a.long - a.short)),
    [data],
  );
  const scenarioRows = useMemo(
    () =>
      (data?.scenarios ?? []).map((s) => ({
        id: s.id,
        label: s.label,
        value: s.pnl,
      })),
    [data],
  );

  if (error) {
    return (
      <div className="page">
        <PageHead eyebrow="Risk book" title="Risk" />
        <LoadError error={error} />
      </div>
    );
  }

  const gross = exposureRows.reduce((a, r) => a + r.long - r.short, 0);
  const net = exposureRows.reduce((a, r) => a + r.net, 0);

  return (
    <div className="page">
      <PageHead
        eyebrow="Risk book"
        title="Risk"
        sub={data ? `Central risk book · all sleeves netted · as of ${shortTimeET(data.as_of)} ET` : undefined}
      />

      {data && (
        <section className="panel summary-strip tiles" aria-label="Risk summary">
          <Stat
            className="tile"
            label="Beta to S&P 500"
            value={<span className="num">{num(data.beta, 2)}</span>}
            sub="after hedges"
          />
          <Stat
            className="tile"
            label="Forecast volatility"
            value={<span className="num">{pct(data.forecast_vol, 1)}</span>}
            sub={`target ${pct(data.target_vol, 0)}, annualized`}
          />
          <Stat
            className="tile"
            label="1-day VaR, 95%"
            value={<span className="num">{money(data.var_95_1d * data.nav)}</span>}
            sub={`${pct(data.var_95_1d, 2)} of NAV, historical`}
          />
          <Stat
            className="tile"
            label="Expected shortfall, 97.5%"
            value={<span className="num">{money(data.es_975_1d * data.nav)}</span>}
            sub={`${pct(data.es_975_1d, 2)} of NAV`}
          />
          <Stat
            className="tile"
            label="Gross / net"
            value={<span className="num">{pct(gross, 0)}</span>}
            sub={`net ${pct(net, 0)}`}
          />
          <Stat
            className="tile"
            label="Top 5 positions"
            value={<span className="num">{pct(data.top5_weight, 0)}</span>}
            sub="of NAV"
          />
        </section>
      )}

      <div className="overview-cols">
        <div className="stack">
          <Figure
            title="Exposure by asset class"
            meta="% of NAV, futures at notional"
            control="risk.exposure"
            chart={
              <DivergingBars
                rows={exposureRows}
                format={(v) => pct(v, 1, true)}
                ariaLabel="Long and short exposure by asset class"
                control="risk.exposure.plot"
              />
            }
            table={{
              head: ["Asset class", "Long", "Short", "Net"],
              numeric: [1, 2, 3],
              rows: exposureRows.map((r) => [r.label, pct(r.long, 1), pct(r.short, 1), pct(r.net, 1, true)]),
            }}
          />
          <Panel title="Sleeves" meta="each sleeve declares a mandate; the CRB hedges only exposure outside it" flush>
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>Sleeve</th>
                    <th className="mandate">Mandate</th>
                    <th className="r">Gross</th>
                    <th className="r">Net</th>
                    <th className="r">Day P&amp;L</th>
                  </tr>
                </thead>
                <tbody>
                  {data?.by_sleeve.map((s) => (
                    <tr key={s.sleeve}>
                      <td className="sym">{s.sleeve}</td>
                      <td className="secondary mandate">{s.mandate}</td>
                      <td className="r">{pct(s.gross, 1)}</td>
                      <td className="r">{pct(s.net, 1, true)}</td>
                      <td className="r">
                        <Delta value={s.day_pnl} kind="money" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
          <Figure
            title="Stress scenarios"
            meta="instant shock to today's book, P&L in $"
            control="risk.scenarios"
            chart={
              <SignedBars
                rows={scenarioRows}
                format={(v) => money(v, { sign: true, compact: true })}
                ariaLabel="Profit and loss under stress scenarios"
                control="risk.scenarios.plot"
              />
            }
            table={{
              head: ["Scenario", "P&L", "% of NAV"],
              numeric: [1, 2],
              rows: (data?.scenarios ?? []).map((s) => [
                s.label,
                money(s.pnl, { sign: true }),
                pct(s.pnl_pct, 2, true),
              ]),
            }}
          />
        </div>
        <div className="stack">
          <Panel
            title="Limits"
            meta={data ? `${data.limits.filter((l) => l.status !== "ok").length} need attention` : undefined}
          >
            {data?.limits.map((l) => (
              <Meter
                key={l.id}
                label={l.label}
                value={l.value}
                limit={l.limit}
                status={l.status}
                valueText={limitText(l.value, l.unit)}
                limitText={limitText(l.limit, l.unit)}
                detail={l.detail}
              />
            ))}
          </Panel>
        </div>
      </div>
    </div>
  );
}
