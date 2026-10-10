import { useMemo, useState } from "react";
import { useBenchmarks, usePerformance } from "../api/hooks";
import type { Schemas } from "../api/client";
import { LineChart, monthTicks } from "../charts/LineChart";
import { Delta, LoadError, PageHead, Panel, Segmented, Stat } from "../components/bits";
import { Figure } from "../components/Figure";
import { dateLong, monthName, num, pct } from "../lib/format";
import { MIN_RATIO_OBS } from "../lib/labels";

type Range = Schemas["PerformanceRange"];
const RANGES: { value: Range; label: string }[] = [
  { value: "1M", label: "1M" },
  { value: "3M", label: "3M" },
  { value: "6M", label: "6M" },
  { value: "YTD", label: "YTD" },
  { value: "1Y", label: "1Y" },
  { value: "ITD", label: "Since inception" },
];

type MonthlyView = "portfolio" | "benchmark" | "excess";
const MONTHLY_VIEWS = [
  { value: "portfolio", label: "Portfolio" },
  { value: "benchmark", label: "Benchmark" },
  { value: "excess", label: "Excess" },
] as const;

export function Performance() {
  const [range, setRange] = useState<Range>("ITD");
  const [benchmark, setBenchmark] = useState("SPY");
  const { data, error, isPlaceholderData } = usePerformance(range, benchmark);
  const benchmarks = useBenchmarks().data ?? [];

  const view = useMemo(() => {
    if (!data) return null;
    const dates = data.series.map((p) => p.date);
    return {
      dates,
      port: data.series.map((p) => p.portfolio),
      bench: data.series.map((p) => p.benchmark),
      dd: data.drawdown.map((p) => p.value * 100),
      ticks: monthTicks(dates, 9),
    };
  }, [data]);

  const s = data?.stats;
  const benchLabel = data?.benchmark.label ?? "Benchmark";

  return (
    <div className="page">
      <PageHead
        title="Performance"
        sub={data ? `${dateLong(data.start)} to ${dateLong(data.end)} · total return, after costs` : undefined}
      >
        <Segmented value={range} options={RANGES} onChange={setRange} label="Period" control="performance.range" />
        <label className="field">
          Benchmark
          <select
            className="select"
            value={benchmark}
            onChange={(e) => setBenchmark(e.target.value)}
            data-control="performance.benchmark"
          >
            {benchmarks.map((b) => (
              <option key={b.id} value={b.id}>
                {b.label}
              </option>
            ))}
          </select>
        </label>
      </PageHead>

      {error && <LoadError error={error} />}

      {s && (
        <section
          className="panel summary-strip"
          aria-label="Statistics"
          style={{ opacity: isPlaceholderData ? 0.55 : 1 }}
        >
          <Stat
            label="Total return"
            value={<Delta value={s.total_return} />}
            sub={
              <>
                {benchLabel} <Delta value={s.benchmark_total_return} />
              </>
            }
          />
          <Stat
            label="Annualized"
            value={<Delta value={s.ann_return} />}
            sub={s.ann_return === null ? "not annualized under a year" : `vol ${pct(s.ann_vol, 1)}`}
          />
          <Stat
            label="Sharpe"
            value={<span className="num">{num(s.sharpe?.value, 2)}</span>}
            sub={
              s.sharpe
                ? `95% CI ${num(s.sharpe.ci_low, 1)} to ${num(s.sharpe.ci_high, 1)}`
                : `needs ${MIN_RATIO_OBS}+ days; have ${s.observations}`
            }
          />
          <Stat
            label="Sortino"
            value={<span className="num">{num(s.sortino, 2)}</span>}
            sub={s.sortino === null ? `needs ${MIN_RATIO_OBS}+ days` : undefined}
          />
          <Stat label="Max drawdown" value={<Delta value={s.max_drawdown} decimals={1} />} />
          <Stat
            label={`Beta to ${benchLabel}`}
            value={<span className="num">{num(s.beta, 2)}</span>}
            sub={
              s.beta === null ? (
                `needs ${MIN_RATIO_OBS}+ days`
              ) : (
                <>
                  alpha <Delta value={s.alpha_ann} decimals={1} /> /yr
                </>
              )
            }
          />
          <Stat
            label="Tracking error"
            value={<span className="num">{pct(s.tracking_error, 1)}</span>}
            sub={
              s.tracking_error === null ? `needs ${MIN_RATIO_OBS}+ days` : `info ratio ${num(s.information_ratio, 2)}`
            }
          />
          <Stat
            label="Up days"
            value={<span className="num">{pct(s.hit_rate, 0)}</span>}
            sub={
              <>
                best <Delta value={s.best_day.value} decimals={1} /> · worst{" "}
                <Delta value={s.worst_day.value} decimals={1} />
              </>
            }
          />
        </section>
      )}

      <Figure
        title={`Growth of $100 vs ${benchLabel}`}
        meta="total return, after costs"
        control="performance.growth"
        stale={isPlaceholderData}
        chart={
          view &&
          s && (
            <LineChart
              labels={view.dates}
              series={[
                {
                  id: "portfolio",
                  label: "Portfolio",
                  color: "var(--series-1)",
                  values: view.port,
                  endText: pct(s.total_return, 1, true),
                },
                {
                  id: "benchmark",
                  label: benchLabel,
                  color: "var(--bench)",
                  values: view.bench,
                  endText: pct(s.benchmark_total_return, 1, true),
                },
              ]}
              baseline={100}
              yFormat={(v) => num(v, 0)}
              xLabel={(i) => dateLong(view.dates[i]!)}
              xTicks={view.ticks}
              height={300}
              ariaLabel={`Growth of $100, portfolio versus ${benchLabel}`}
              control="performance.growth.plot"
            />
          )
        }
        table={{
          head: ["Date", "Portfolio", benchLabel],
          numeric: [1, 2],
          rows: (data?.series ?? [])
            .filter((_, i, a) => i % 5 === 0 || i === a.length - 1)
            .map((p) => [p.date, num(p.portfolio, 2), num(p.benchmark, 2)]),
        }}
      />

      <Figure
        title="Drawdown"
        meta="from running peak"
        control="performance.drawdown"
        stale={isPlaceholderData}
        chart={
          view && (
            <LineChart
              labels={view.dates}
              series={[{ id: "dd", label: "Drawdown", color: "var(--down)", values: view.dd, area: true }]}
              baseline={0}
              yMax={0}
              yFormat={(v) => `${num(v, 0)}%`}
              xLabel={(i) => dateLong(view.dates[i]!)}
              xTicks={view.ticks}
              height={160}
              ariaLabel="Portfolio drawdown from its running peak"
              control="performance.drawdown.plot"
            />
          )
        }
        table={{
          head: ["Date", "Drawdown"],
          numeric: [1],
          rows: (data?.drawdown ?? [])
            .filter((_, i, a) => i % 5 === 0 || i === a.length - 1)
            .map((p) => [p.date, pct(p.value, 2)]),
        }}
      />

      <MonthlyGrid monthly={data?.monthly ?? []} benchLabel={benchLabel} />
    </div>
  );
}

function MonthlyGrid({ monthly, benchLabel }: { monthly: Schemas["Performance"]["monthly"]; benchLabel: string }) {
  const [view, setView] = useState<MonthlyView>("portfolio");
  const years = useMemo(() => [...new Set(monthly.map((m) => m.year))].sort(), [monthly]);
  const value = (m: Schemas["Performance"]["monthly"][number]) =>
    view === "portfolio" ? m.portfolio : view === "benchmark" ? m.benchmark : m.portfolio - m.benchmark;
  const maxAbs = Math.max(0.02, ...monthly.map((m) => Math.abs(value(m))));

  return (
    <Panel
      title="Monthly returns"
      meta={
        view === "excess"
          ? `portfolio minus ${benchLabel}`
          : view === "benchmark"
            ? benchLabel
            : "portfolio, since inception"
      }
      actions={
        <Segmented
          value={view}
          options={MONTHLY_VIEWS}
          onChange={setView}
          label="Monthly view"
          control="performance.monthly"
        />
      }
      flush
    >
      <div className="table-wrap">
        <table className="table monthly">
          <thead>
            <tr>
              <th>Year</th>
              {Array.from({ length: 12 }, (_, i) => (
                <th key={i} className="r">
                  {monthName(i + 1)}
                </th>
              ))}
              <th className="r">Year total</th>
            </tr>
          </thead>
          <tbody>
            {years.map((y) => {
              const months = monthly.filter((m) => m.year === y);
              const compound = (f: (m: (typeof months)[number]) => number) =>
                months.reduce((g, m) => g * (1 + f(m)), 1) - 1;
              // Excess for the year is the difference of compounded returns, not compounded differences.
              const yearRet =
                view === "excess" ? compound((m) => m.portfolio) - compound((m) => m.benchmark) : compound(value);
              return (
                <tr key={y}>
                  <td className="sym">{y}</td>
                  {Array.from({ length: 12 }, (_, i) => {
                    const m = months.find((x) => x.month === i + 1);
                    if (!m) return <td key={i} className="r muted" />;
                    const v = value(m);
                    const alpha = Math.min(1, Math.abs(v) / maxAbs) * 0.28;
                    return (
                      <td
                        key={i}
                        className="r heat"
                        style={{
                          background: `color-mix(in srgb, ${v >= 0 ? "var(--up)" : "var(--down)"} ${Math.round(alpha * 100)}%, transparent)`,
                        }}
                      >
                        {pct(v, 1, true)}
                      </td>
                    );
                  })}
                  <td className="r year-total">
                    <Delta value={yearRet} decimals={1} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}
