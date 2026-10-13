import { useMemo } from "react";
import type { Schemas } from "../api/client";
import { dateLong, money, num, pct } from "../lib/format";
import { dollarTicks, tickDecimals } from "./axis";
import { type EquityMode, equityLines } from "./equity";
import { LineChart, monthTicks } from "./LineChart";

/** Portfolio against its benchmark, as growth of $100 or as NAV in dollars. */
export function EquityChart({
  data,
  mode,
  height,
  maxTicks,
  ariaLabel,
  control,
}: {
  data: Schemas["Performance"];
  mode: EquityMode;
  height: number;
  maxTicks: number;
  ariaLabel: string;
  control: string;
}) {
  const view = useMemo(() => {
    const dates = data.series.map((p) => p.date);
    return { dates, ...equityLines(data.series, mode), ticks: monthTicks(dates, maxTicks) };
  }, [data, mode, maxTicks]);
  const bench = data.benchmark.label;
  const nav = mode === "nav";
  const endText = (values: number[], ret: number) => (nav ? money(values[values.length - 1]) : pct(ret, 1, true));

  return (
    <LineChart
      series={[
        {
          id: "portfolio",
          label: "Portfolio",
          legend: nav ? "Portfolio NAV" : undefined,
          color: "var(--series-1)",
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
        },
      ]}
      baseline={view.base}
      yFormat={nav ? (v, ticks) => dollarTicks(ticks)(v) : (v, ticks) => num(v, tickDecimals(ticks))}
      tipFormat={nav ? (v) => money(v) : undefined}
      xLabel={(i) => dateLong(view.dates[i]!)}
      xTicks={view.ticks}
      height={height}
      ariaLabel={ariaLabel}
      control={control}
    />
  );
}
