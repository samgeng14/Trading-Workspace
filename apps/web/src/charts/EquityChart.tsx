import { useMemo } from "react";
import type { Schemas } from "../api/client";
import { dateLong, money, num, pct } from "../lib/format";
import { Delta } from "../components/bits";
import { dollarTicks, tickDecimals } from "./axis";
import { type EquityMode, equityLines } from "./equity";
import { LineChart, monthTicks } from "./LineChart";

/** Portfolio against its benchmark: cumulative return, growth of $100, or NAV in dollars. */
export function EquityChart({
  data,
  mode,
  height,
  maxTicks,
  ariaLabel,
  control,
  headline,
}: {
  data: Schemas["Performance"];
  mode: EquityMode;
  height: number;
  maxTicks: number;
  ariaLabel: string;
  control: string;
  /** Caption under the big total-return figure, e.g. "Total return since inception"; no figure when absent. */
  headline?: string;
}) {
  const view = useMemo(() => {
    const dates = data.series.map((p) => p.date);
    return { dates, ...equityLines(data.series, mode), ticks: monthTicks(dates, maxTicks) };
  }, [data, mode, maxTicks]);
  const bench = data.benchmark.label;
  const nav = mode === "nav";
  const ret = mode === "return";
  const endText = (values: number[], r: number) => (nav ? money(values[values.length - 1]) : pct(r, 1, true));

  return (
    <>
      {headline && (
        <div className="equity-headline">
          <Delta value={data.stats.total_return} className="equity-headline-value" />
          <span className="equity-headline-caption">{headline}</span>
        </div>
      )}
      <LineChart
        series={[
          {
            id: "portfolio",
            label: "Portfolio",
            legend: nav ? "Portfolio NAV" : undefined,
            color: "var(--series-1)",
            glow: true,
            values: view.portfolio,
            endText: endText(view.portfolio, data.stats.total_return),
          },
          {
            id: "benchmark",
            label: bench,
            legend: nav ? `${bench}, from the same starting NAV` : undefined,
            color: "var(--bench)",
            values: view.benchmark,
            endText: endText(view.benchmark, data.stats.benchmark_total_return),
            fill: false,
          },
        ]}
        baseline={view.base}
        signedFill={ret}
        yFormat={
          nav
            ? (v, ticks) => dollarTicks(ticks)(v)
            : ret
              ? (v, ticks) => pct(v, tickDecimals(ticks, 0.01), true)
              : (v, ticks) => num(v, tickDecimals(ticks))
        }
        tipFormat={nav ? (v) => money(v) : ret ? (v) => pct(v, 2, true) : undefined}
        xLabel={(i) => dateLong(view.dates[i]!)}
        xTicks={view.ticks}
        height={height}
        ariaLabel={ariaLabel}
        control={control}
      />
    </>
  );
}
