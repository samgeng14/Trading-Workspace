import { useMemo } from "react";
import { Link } from "react-router";
import { useIntraday, useOrders, usePerformance, usePositions, useRisk, useStatus, useSummary } from "../api/hooks";
import type { Schemas } from "../api/client";
import { DivergingBars } from "../charts/DivergingBars";
import { LineChart, monthTicks } from "../charts/LineChart";
import { Sparkline } from "../charts/Sparkline";
import { Delta, LoadError, Meter, Panel, Stat } from "../components/bits";
import { NextJob, ScheduleList } from "../components/Schedule";
import { Figure } from "../components/Figure";
import { type Column, SortableTable } from "../components/SortableTable";
import { dateLong, dateShort, money, num, pct, price, shortTimeET } from "../lib/format";
import { ASSET_CLASS_LABEL, limitText } from "../lib/labels";
import { useServerNow } from "../lib/serverClock";

export function Overview() {
  return (
    <div className="page">
      <KpiBand />
      {/* Two independent stacks so a tall panel never leaves a gap beside it. */}
      <div className="overview-cols">
        <div className="stack">
          <IntradayPanel className="" />
          <MoversPanel className="" />
          <PerformancePanel className="" />
        </div>
        <div className="stack">
          <ExposurePanel className="" />
          <LimitsPanel className="" />
          <TodayPanel className="" />
        </div>
      </div>
    </div>
  );
}

// ── KPI band ───────────────────────────────────────────────────────────

function KpiBand() {
  const { data: s, error } = useSummary();
  const status = useStatus().data;
  if (error) return <LoadError error={error} />;
  if (!s) return <div className="kpi-band panel" aria-busy="true" style={{ minHeight: 92 }} />;
  const live = status?.market.session === "open";
  return (
    <section className="kpi-band panel" aria-label="Portfolio summary">
      <div className="kpi-hero">
        <span className="stat-label">Net liquidation value</span>
        <div className="kpi-hero-row">
          <span className="hero-value" data-testid="nav">
            {money(s.nav)}
          </span>
          <Sparkline values={s.nav_history.map((p) => p.value)} label="NAV, last 30 sessions" />
        </div>
        <span className="stat-sub">
          {live ? `Live, ${shortTimeET(s.as_of)} ET` : `Close, ${status ? dateLong(status.market.trading_date) : ""}`} ·
          paper since {dateShort(s.inception_date)}, {s.inception_date.slice(0, 4)}
        </span>
      </div>
      <Stat
        label="Day P&L"
        value={<Delta value={s.day_pnl} kind="money" />}
        sub={<Delta value={s.day_return} />}
        testId="kpi-day"
      />
      <Stat label="Month to date" value={<Delta value={s.mtd_return} />} />
      <Stat label="Year to date" value={<Delta value={s.ytd_return} />} />
      <Stat
        label="Since inception"
        value={<Delta value={s.itd_return} />}
        sub={
          <>
            {s.benchmark.label} <Delta value={s.benchmark_itd_return} />
          </>
        }
      />
      <Stat
        label="Gross / net exposure"
        value={<span className="num">{pct(s.gross_exposure, 0)}</span>}
        sub={<span className="num">net {pct(s.net_exposure, 0)}</span>}
      />
      <Stat label="Beta" value={<span className="num">{num(s.beta, 2)}</span>} sub="to S&P 500, hedged" />
      <Stat
        label="Sharpe, since inception"
        value={<span className="num">{num(s.sharpe_itd.value, 2)}</span>}
        sub={
          <>
            95% CI {num(s.sharpe_itd.ci_low, 1)} to {num(s.sharpe_itd.ci_high, 1)}
            <br />
            {num(s.sharpe_itd.years, 1)} yr of returns
          </>
        }
      />
    </section>
  );
}

// ── today's P&L ────────────────────────────────────────────────────────

const HOURS = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"];

function IntradayPanel({ className }: { className: string }) {
  const { data, error } = useIntraday();
  const view = useMemo(() => {
    if (!data) return null;
    const open = Date.parse(data.session_open);
    const minutes = Math.round((Date.parse(data.session_close) - open) / 60_000);
    const values = data.points.map((p) => p.pnl);
    const ticks = HOURS.map((h) => {
      const [hh, mm] = h.split(":").map(Number) as [number, number];
      return { index: (hh - 9) * 60 + mm - 30, text: h };
    }).filter((t) => t.index <= minutes);
    const last = values[values.length - 1] ?? 0;
    return { open, minutes, values, ticks, last };
  }, [data]);

  if (error)
    return (
      <Panel title="Today's P&L" className={className}>
        <LoadError error={error} />
      </Panel>
    );
  return (
    <Figure
      className={className}
      title="Today's P&L"
      meta={data ? `${dateLong(data.trading_date)} · ${data.live ? "live" : "session closed"}` : undefined}
      control="overview.intraday"
      chart={
        view && (
          <LineChart
            labels={view.values.map((_, i) => String(i))}
            series={[
              {
                id: "pnl",
                label: "Day P&L",
                color: "var(--ink-1)",
                values: view.values,
                endText: money(view.last, { sign: true }),
              },
            ]}
            baseline={0}
            signedFill
            xLength={view.minutes + 1}
            yFormat={(v) => money(v, { compact: true, sign: true })}
            xLabel={(i) => `${shortTimeET(new Date(view.open + i * 60_000))} ET`}
            xTicks={view.ticks}
            height={220}
            ariaLabel="Profit and loss through today's session"
            control="overview.intraday.plot"
          />
        )
      }
      table={{
        head: ["Time (ET)", "P&L"],
        numeric: [1],
        rows: (data?.points ?? [])
          .filter((_, i) => i % 15 === 0)
          .map((p) => [shortTimeET(p.t), money(p.pnl, { sign: true })]),
      }}
    />
  );
}

// ── exposure ───────────────────────────────────────────────────────────

function ExposurePanel({ className }: { className: string }) {
  const { data, error } = useRisk();
  const rows = useMemo(
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
  if (error)
    return (
      <Panel title="Exposure" className={className}>
        <LoadError error={error} />
      </Panel>
    );
  return (
    <Figure
      className={className}
      title="Exposure"
      meta="% of NAV"
      control="overview.exposure"
      actions={
        <Link to="/risk" className="btn btn-ghost" data-control="overview.exposure.more">
          Risk →
        </Link>
      }
      chart={
        <DivergingBars
          rows={rows}
          format={(v) => pct(v, 1, true)}
          ariaLabel="Long and short exposure by asset class"
          control="overview.exposure.plot"
        />
      }
      table={{
        head: ["Asset class", "Long", "Short", "Net"],
        numeric: [1, 2, 3],
        rows: rows.map((r) => [r.label, pct(r.long, 1), pct(r.short, 1), pct(r.net, 1, true)]),
      }}
    />
  );
}

// ── movers ─────────────────────────────────────────────────────────────

type Position = Schemas["Position"];

const moverColumns: Column<Position>[] = [
  {
    id: "symbol",
    header: "Symbol",
    accessorKey: "symbol",
    cell: ({ row }) => (
      <span>
        <span className="sym">{row.original.symbol}</span> <span className="sub">{row.original.name}</span>
      </span>
    ),
  },
  {
    id: "qty",
    header: "Qty",
    accessorKey: "quantity",
    meta: { align: "right" },
    cell: ({ row }) => num(row.original.quantity),
  },
  {
    id: "last",
    header: "Last",
    accessorKey: "last",
    meta: { align: "right" },
    cell: ({ row }) => price(row.original.last),
  },
  {
    id: "day_return",
    header: "Day %",
    accessorKey: "day_return",
    meta: { align: "right" },
    cell: ({ row }) => <Delta value={row.original.day_return} />,
  },
  {
    id: "day_pnl",
    header: "Day P&L",
    accessorFn: (p) => Math.abs(p.day_pnl),
    meta: { align: "right" },
    cell: ({ row }) => <Delta value={row.original.day_pnl} kind="money" decimals={0} />,
  },
  {
    id: "weight",
    header: "Weight",
    accessorKey: "weight",
    meta: { align: "right" },
    cell: ({ row }) => pct(row.original.weight, 1),
  },
];

function MoversPanel({ className }: { className: string }) {
  const { data, error } = usePositions();
  const top = useMemo(
    () => [...(data?.positions ?? [])].sort((a, b) => Math.abs(b.day_pnl) - Math.abs(a.day_pnl)).slice(0, 10),
    [data],
  );
  return (
    <Panel
      title="Biggest P&L today"
      meta={data ? `top 10 of ${data.positions.length} positions by |day P&L|` : undefined}
      className={className}
      flush
      actions={
        <Link to="/positions" className="btn btn-ghost" data-control="overview.movers.more">
          All positions →
        </Link>
      }
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        <SortableTable
          data={top}
          columns={moverColumns}
          control="overview.movers"
          rowKey={(p) => p.symbol}
          initialSort={[{ id: "day_pnl", desc: true }]}
        />
      )}
    </Panel>
  );
}

// ── limits ─────────────────────────────────────────────────────────────

function LimitsPanel({ className }: { className: string }) {
  const { data, error } = useRisk();
  const breaches = data?.limits.filter((l) => l.status !== "ok").length ?? 0;
  return (
    <Panel
      title="Risk limits"
      meta={data ? (breaches === 0 ? "all within limits" : `${breaches} need attention`) : undefined}
      className={className}
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        data?.limits.map((l) => (
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
        ))
      )}
    </Panel>
  );
}

// ── performance ────────────────────────────────────────────────────────

function PerformancePanel({ className }: { className: string }) {
  const { data, error } = usePerformance("ITD", "SPY");
  const view = useMemo(() => {
    if (!data) return null;
    const dates = data.series.map((p) => p.date);
    return {
      dates,
      port: data.series.map((p) => p.portfolio),
      bench: data.series.map((p) => p.benchmark),
      ticks: monthTicks(dates, 7),
    };
  }, [data]);
  if (error)
    return (
      <Panel title="Since inception" className={className}>
        <LoadError error={error} />
      </Panel>
    );
  return (
    <Figure
      className={className}
      title="Since inception vs S&P 500"
      meta={data ? `growth of $100, total return, through ${dateShort(data.end)} close` : undefined}
      control="overview.performance"
      actions={
        <Link to="/performance" className="btn btn-ghost" data-control="overview.performance.more">
          Performance →
        </Link>
      }
      chart={
        view &&
        data && (
          <LineChart
            labels={view.dates}
            series={[
              {
                id: "portfolio",
                label: "Portfolio",
                color: "var(--series-1)",
                values: view.port,
                endText: pct(data.stats.total_return, 1, true),
              },
              {
                id: "benchmark",
                label: data.benchmark.label,
                color: "var(--bench)",
                values: view.bench,
                endText: pct(data.stats.benchmark_total_return, 1, true),
              },
            ]}
            baseline={100}
            yFormat={(v) => num(v, 0)}
            xLabel={(i) => dateLong(view.dates[i]!)}
            xTicks={view.ticks}
            height={220}
            ariaLabel="Portfolio versus S&P 500 since inception"
            control="overview.performance.plot"
          />
        )
      }
      table={{
        head: ["Month", "Portfolio", data?.benchmark.label ?? "Benchmark"],
        numeric: [1, 2],
        rows: (data?.monthly ?? []).map((m) => [
          `${m.year}-${String(m.month).padStart(2, "0")}`,
          pct(m.portfolio, 2, true),
          pct(m.benchmark, 2, true),
        ]),
      }}
    />
  );
}

// ── today ──────────────────────────────────────────────────────────────

function TodayPanel({ className }: { className: string }) {
  const status = useStatus().data;
  const orders = useOrders().data;
  const now = useServerNow();
  const counts = useMemo(() => {
    const list = orders?.orders ?? [];
    return {
      filled: list.filter((o) => o.status === "filled").length,
      working: list.filter((o) => ["pending", "submitted", "partial"].includes(o.status)).length,
      notional: list.filter((o) => o.status === "filled").reduce((a, o) => a + o.quantity * (o.avg_fill_price ?? 0), 0),
    };
  }, [orders]);
  const next = status?.schedule.find((j) => j.status === "scheduled");
  const day = status?.schedule[0] ? dateLong(new Date(status.schedule[0].at).toISOString().slice(0, 10)) : "";
  return (
    <Panel
      title="Schedule"
      meta={day}
      className={className}
      flush
      actions={
        <Link to="/trading" className="btn btn-ghost" data-control="overview.today.more">
          Trading →
        </Link>
      }
      foot={
        <span className="num">
          Orders {orders ? dateShort(orders.trading_date) : ""}: {counts.filled} filled (
          {money(counts.notional, { compact: true })}), {counts.working} working
        </span>
      }
    >
      <NextJob job={next} now={now} />
      <ScheduleList jobs={status?.schedule ?? []} />
    </Panel>
  );
}
