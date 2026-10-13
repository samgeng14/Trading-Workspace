import { useMemo, useState } from "react";
import { Link } from "react-router";
import { useIntraday, useOrders, usePerformance, usePositions, useRisk, useStatus, useSummary } from "../api/hooks";
import type { Schemas } from "../api/client";
import { DivergingBars } from "../charts/DivergingBars";
import { sessionMinutes } from "../charts/axis";
import { EquityChart } from "../charts/EquityChart";
import { EQUITY_MODES, type EquityMode, equityLines } from "../charts/equity";
import { LineChart } from "../charts/LineChart";
import { Sparkline } from "../charts/Sparkline";
import { Delta, LoadError, Meter, Panel, Segmented, Stat } from "../components/bits";
import { StatusIcon } from "../components/icons";
import { HistoryEmpty, isShortHistory } from "../components/HistoryEmpty";
import { NextJob, ScheduleList } from "../components/Schedule";
import { Figure } from "../components/Figure";
import { type Column, SortableTable } from "../components/SortableTable";
import { dateLong, dateShort, money, num, pct, price, shortTimeET, timeET } from "../lib/format";
import { ASSET_CLASS_LABEL, limitText, MIN_RATIO_OBS } from "../lib/labels";
import { type CheckState, type Issue, VERDICT_TEXT } from "../lib/readiness";
import { useReadiness } from "../lib/useReadiness";
import { useServerNow } from "../lib/serverClock";

export function Overview() {
  return (
    <div className="page">
      <CommandHead />
      <KpiBand />
      <ReadinessRow />
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

// ── head: what this page is for, and the one-line verdict ──────────────

const VERDICT_TONE = { clear: "good", review: "warn", blocked: "bad", loading: "neutral" } as const;

function CommandHead() {
  const status = useStatus().data;
  const ready = useReadiness();
  const now = useServerNow();
  const counts = [
    ready.blockers.length && `${ready.blockers.length} blocker${ready.blockers.length === 1 ? "" : "s"}`,
    ready.warnings.length && `${ready.warnings.length} warning${ready.warnings.length === 1 ? "" : "s"}`,
  ].filter(Boolean);
  return (
    <header className="page-head">
      <div>
        <h1 className="page-title">Command Center</h1>
        <p className="page-sub">
          Daily cockpit. Is the book safe to act on today, what changed, and what needs a person to look at.
        </p>
        <p className="action-line" aria-live="polite" data-testid="verdict">
          <span className={`tone-${VERDICT_TONE[ready.verdict]}`}>
            <span className="action-dot" aria-hidden="true">
              ●
            </span>{" "}
            {VERDICT_TEXT[ready.verdict]}
          </span>
          {counts.length > 0 && <span>{counts.join(" · ")}</span>}
          <span className="num">{timeET(now)} ET</span>
          {status && <span className="tone-accent">{status.mode}</span>}
        </p>
      </div>
    </header>
  );
}

// ── readiness, blockers, warnings ──────────────────────────────────────

const STATE_ICON: Record<CheckState, "ok" | "warning" | "error" | "unknown"> = {
  ok: "ok",
  warn: "warning",
  block: "error",
  unknown: "unknown",
};

function ReadinessRow() {
  const ready = useReadiness();
  return (
    <div className="readiness-row">
      <Panel
        title="Readiness"
        meta={`${ready.checks.filter((c) => c.state === "ok").length} of ${ready.checks.length} ok`}
        flush
      >
        <ul className="checklist">
          {ready.checks.map((c) => (
            <li key={c.id}>
              <Link to={c.to} className="check-row" data-state={c.state} data-control={`overview.readiness.${c.id}`}>
                <span className="badge-icon">
                  <StatusIcon status={STATE_ICON[c.state]} />
                </span>
                <span className="check-label">{c.label}</span>
                <span className="check-value">{c.value}</span>
              </Link>
            </li>
          ))}
        </ul>
      </Panel>
      <div className="stack">
        <IssuePanel
          title="Blockers"
          issues={ready.blockers}
          tone="bad"
          tag="Block"
          empty="None. Nothing is stopping the next rebalance."
        />
        <IssuePanel title="Warnings" issues={ready.warnings} tone="warn" tag="Warn" empty="None." />
      </div>
    </div>
  );
}

function IssuePanel({
  title,
  issues,
  tone,
  tag,
  empty,
}: {
  title: string;
  issues: Issue[];
  tone: "bad" | "warn";
  tag: string;
  empty: string;
}) {
  return (
    <Panel
      title={title}
      actions={
        <span
          className="tag"
          data-tone={issues.length ? tone : "neutral"}
          aria-label={`${issues.length} ${title.toLowerCase()}`}
        >
          {issues.length}
        </span>
      }
      flush
    >
      {issues.length === 0 ? (
        <p className="issue-empty">{empty}</p>
      ) : (
        <ul className="issues">
          {issues.map((i) => (
            <li key={i.id}>
              <Link to={i.to} className="issue" data-control={`overview.issue.${i.id}`}>
                <span className="tag" data-tone={tone}>
                  {tag}
                </span>
                <span className="issue-text">
                  <strong>{i.title}</strong> <span className="issue-detail">{i.detail}</span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
  );
}

// ── KPI band ───────────────────────────────────────────────────────────

function KpiBand() {
  const { data: s, error } = useSummary();
  const status = useStatus().data;
  if (error) return <LoadError error={error} />;
  if (!s) return <div className="kpi-band panel" aria-busy="true" style={{ minHeight: 150 }} />;
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
          since {dateShort(s.inception_date)}, {s.inception_date.slice(0, 4)}
          {!s.history.complete && (
            <span className="history-note">
              {" "}
              · history so far: {s.history.days} days. Load the rest: bin/tradeit flex
            </span>
          )}
        </span>
      </div>
      <div className="kpi-grid">
        <Stat
          label="Day P&L"
          value={<Delta value={s.day_pnl} kind="money" />}
          sub={<Delta value={s.day_return} />}
          testId="kpi-day"
        />
        <Stat
          label="Month to date"
          value={<Delta value={s.mtd_return} />}
          sub={s.mtd_return === null ? "history doesn't reach the month start" : undefined}
        />
        <Stat
          label="Year to date"
          value={<Delta value={s.ytd_return} />}
          sub={s.ytd_return === null ? "history doesn't reach the year start" : undefined}
        />
        <Stat
          label="Since inception"
          value={<Delta value={s.itd_return} />}
          sub={
            s.itd_return === null ? (
              "needs 2+ days of NAV"
            ) : (
              <>
                {s.benchmark.label} <Delta value={s.benchmark_itd_return} />
              </>
            )
          }
        />
        <Stat
          label="Gross exposure"
          value={<span className="num">{pct(s.gross_exposure, 0)}</span>}
          sub={
            <>
              net <span className="num">{pct(s.net_exposure, 0)}</span> · short{" "}
              <span className="num">{pct(s.short_exposure, 0)}</span>
            </>
          }
        />
        <Stat label="Beta" value={<span className="num">{num(s.beta, 2)}</span>} sub="to S&P 500, hedged" />
        <Stat
          label="Sharpe, inception"
          value={<span className="num">{num(s.sharpe_itd?.value, 2)}</span>}
          sub={
            s.sharpe_itd ? (
              <>
                95% CI {num(s.sharpe_itd.ci_low, 1)} to {num(s.sharpe_itd.ci_high, 1)} · {num(s.sharpe_itd.years, 1)} yr
              </>
            ) : (
              `needs ${MIN_RATIO_OBS}+ days of NAV; have ${Math.max(0, s.history.days - 1)}`
            )
          }
        />
        <Stat
          label="Cash"
          value={<span className="num">{money(s.cash)}</span>}
          sub={`${s.positions_count} positions`}
        />
      </div>
    </section>
  );
}

// ── today's P&L ────────────────────────────────────────────────────────

const HOURS = ["10:00", "11:00", "12:00", "13:00", "14:00", "15:00", "16:00"];

/** "09:30" on the minute, "14:12:15" between minutes (the API samples on its own clock). */
const sampleTime = (t: string) => {
  const s = timeET(t);
  return s.endsWith(":00") ? s.slice(0, 5) : s;
};

function IntradayPanel({ className }: { className: string }) {
  const { data, error } = useIntraday();
  const view = useMemo(() => {
    if (!data) return null;
    // x comes from each point's own timestamp; the axis always spans the whole session.
    const points = sessionMinutes(data.points, data.session_open, data.session_close);
    const minutes = (Date.parse(data.session_close) - Date.parse(data.session_open)) / 60_000;
    const ticks = HOURS.map((h) => {
      const [hh, mm] = h.split(":").map(Number) as [number, number];
      return { at: (hh - 9) * 60 + mm - 30, text: h };
    }).filter((t) => t.at <= minutes);
    const x = { values: points.map((p) => p.minute), domain: [0, minutes] as [number, number] };
    return { points, x, ticks, last: points[points.length - 1]?.pnl ?? 0 };
  }, [data]);

  if (error)
    return (
      <Panel title="Today's P&L" className={className}>
        <LoadError error={error} />
      </Panel>
    );
  if (data && view?.points.length === 0)
    return (
      <Panel title="Today's P&L" meta={dateLong(data.trading_date)} className={className}>
        <p className="empty">
          No intraday P&L recorded for this session. The API samples P&L while the market is open and it is connected to
          IBKR.
        </p>
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
            series={[
              {
                id: "pnl",
                label: "Day P&L",
                color: "var(--ink-1)",
                values: view.points.map((p) => p.pnl),
                endText: money(view.last, { sign: true }),
              },
            ]}
            baseline={0}
            signedFill
            x={view.x}
            yFormat={(v) => money(v, { compact: true, sign: true })}
            tipFormat={(v) => money(v, { sign: true })}
            xLabel={(i) => `${sampleTime(view.points[i]!.t)} ET`}
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
        // the first sample in each quarter hour, and the latest
        rows: (view?.points ?? [])
          .filter(
            (p, i, a) => i === 0 || i === a.length - 1 || Math.floor(p.minute / 15) > Math.floor(a[i - 1]!.minute / 15),
          )
          .map((p) => [sampleTime(p.t), money(p.pnl, { sign: true })]),
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
  const history = useSummary().data?.history;
  const [mode, setMode] = useState<EquityMode>("growth");
  const monthEnds = useMemo(() => {
    const series = data?.series ?? [];
    const lines = equityLines(series, "nav");
    // the last close of each month, and the latest
    return series.flatMap((p, i) =>
      i === series.length - 1 || series[i + 1]!.date.slice(0, 7) !== p.date.slice(0, 7)
        ? [[p.date, money(lines.portfolio[i]), money(lines.benchmark[i])]]
        : [],
    );
  }, [data]);
  if (error)
    return (
      <Panel title="Since inception" className={className}>
        {isShortHistory(error) ? <HistoryEmpty history={history} /> : <LoadError error={error} />}
      </Panel>
    );
  const bench = data?.benchmark.label ?? "Benchmark";
  return (
    <Figure
      className={className}
      title="Since inception vs S&P 500"
      meta={
        data
          ? `${mode === "nav" ? "NAV in dollars" : "growth of $100, total return"}, through ${dateShort(data.end)} close`
          : undefined
      }
      control="overview.performance"
      actions={
        // In a narrow panel these wrap onto two lines, so the Chart / Table switch keeps its full width.
        <div className="panel-actions" style={{ flex: "1 1 0", flexWrap: "wrap", justifyContent: "flex-end" }}>
          <Segmented
            value={mode}
            options={EQUITY_MODES}
            onChange={setMode}
            label="Equity curve units"
            control="overview.performance.mode"
          />
          <Link to="/performance" className="btn btn-ghost" data-control="overview.performance.more">
            Performance →
          </Link>
        </div>
      }
      chart={
        data && (
          <EquityChart
            data={data}
            mode={mode}
            height={220}
            maxTicks={7}
            ariaLabel={
              mode === "nav"
                ? `NAV in dollars since inception, versus ${bench} from the same starting NAV`
                : `Portfolio versus ${bench} since inception`
            }
            control="overview.performance.plot"
          />
        )
      }
      table={
        mode === "nav"
          ? { head: ["Date", "Portfolio NAV", `${bench}, same start`], numeric: [1, 2], rows: monthEnds }
          : {
              head: ["Month", "Portfolio", bench],
              numeric: [1, 2],
              rows: (data?.monthly ?? []).map((m) => [
                `${m.year}-${String(m.month).padStart(2, "0")}`,
                pct(m.portfolio, 2, true),
                pct(m.benchmark, 2, true),
              ]),
            }
      }
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
