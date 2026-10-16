import { useMemo, useState } from "react";
import { usePositions, useStatus } from "../api/hooks";
import type { Schemas } from "../api/client";
import { Delta, LoadError, PageHead, Panel, Segmented, Stat } from "../components/bits";
import { SearchIcon } from "../components/icons";
import { type Column, SortableTable } from "../components/SortableTable";
import { money, num, pct, price, shortTimeET } from "../lib/format";
import { ASSET_CLASS_LABEL } from "../lib/labels";

type Position = Schemas["Position"];
type Side = "all" | "long" | "short";

const SIDES = [
  { value: "all", label: "All" },
  { value: "long", label: "Long" },
  { value: "short", label: "Short" },
] as const;

// Colour follows the asset class, in this fixed order, never its rank (dataviz palette slots 1-7).
const CLASS_ORDER = Object.keys(ASSET_CLASS_LABEL) as Schemas["AssetClass"][];
const classColor = (c: Schemas["AssetClass"]) => `var(--cat-${CLASS_ORDER.indexOf(c) + 1})`;

export function Positions() {
  const { data, error } = usePositions();
  const session = useStatus().data?.market.session;
  const [query, setQuery] = useState("");
  const [assetClass, setAssetClass] = useState<string>("all");
  const [sleeve, setSleeve] = useState<string>("all");
  const [side, setSide] = useState<Side>("all");

  const all = useMemo(() => data?.positions ?? [], [data]);
  const classes = useMemo(() => [...new Set(all.map((p) => p.asset_class))], [all]);
  const sleeves = useMemo(() => [...new Set(all.map((p) => p.sleeve))], [all]);

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    return all.filter(
      (p) =>
        (!q || p.symbol.toLowerCase().includes(q) || p.name.toLowerCase().includes(q)) &&
        (assetClass === "all" || p.asset_class === assetClass) &&
        (sleeve === "all" || p.sleeve === sleeve) &&
        (side === "all" || (side === "long" ? p.quantity > 0 : p.quantity < 0)),
    );
  }, [all, query, assetClass, sleeve, side]);

  const totals = useMemo(() => {
    const sum = (f: (p: Position) => number) => rows.reduce((a, p) => a + f(p), 0);
    return {
      long: sum((p) => Math.max(0, p.market_value)),
      short: sum((p) => Math.min(0, p.market_value)),
      mv: sum((p) => p.market_value),
      weight: sum((p) => p.weight),
      dayPnl: sum((p) => p.day_pnl),
      unrealized: sum((p) => p.unrealized_pnl),
    };
  }, [rows]);

  // the largest |weight| sets the scale of the in-cell weight bars
  const maxWeight = useMemo(() => Math.max(0.01, ...all.map((p) => Math.abs(p.weight))), [all]);
  const columns = useMemo(() => buildColumns(totals, maxWeight), [totals, maxWeight]);
  const filtered = rows.length !== all.length;

  return (
    <div className="page">
      <PageHead
        eyebrow="Portfolio"
        title="Positions"
        sub={
          data
            ? `${all.length} positions · NAV ${money(data.nav)} · ${session === "open" ? `live, ${shortTimeET(data.as_of)} ET` : "last close"}`
            : undefined
        }
      >
        <span className="search">
          <SearchIcon />
          <input
            className="input"
            type="search"
            placeholder="Search symbol or name"
            aria-label="Search positions"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            data-control="positions.search"
          />
        </span>
        <label className="field">
          Asset class
          <select
            className="select"
            value={assetClass}
            onChange={(e) => setAssetClass(e.target.value)}
            data-control="positions.asset-class"
          >
            <option value="all">All</option>
            {classes.map((c) => (
              <option key={c} value={c}>
                {ASSET_CLASS_LABEL[c]}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          Sleeve
          <select
            className="select"
            value={sleeve}
            onChange={(e) => setSleeve(e.target.value)}
            data-control="positions.sleeve"
          >
            <option value="all">All</option>
            {sleeves.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
        </label>
        <Segmented value={side} options={SIDES} onChange={setSide} label="Side" control="positions.side" />
      </PageHead>

      {data && (
        <section className="panel summary-strip tiles" aria-label="Totals for the positions shown">
          <Stat
            className="tile"
            label={filtered ? "Long (filtered)" : "Long"}
            value={<span className="num">{money(totals.long, { compact: true })}</span>}
            sub={pct(data ? totals.long / data.nav : 0, 1)}
          />
          <Stat
            className="tile"
            label="Short"
            value={<span className="num">{money(totals.short, { compact: true })}</span>}
            sub={pct(data ? totals.short / data.nav : 0, 1)}
          />
          <Stat
            className="tile"
            label="Net"
            value={<span className="num">{money(totals.mv, { compact: true })}</span>}
            sub={pct(totals.weight, 1)}
          />
          <Stat className="tile" label="Day P&L" value={<Delta value={totals.dayPnl} kind="money" />} />
          <Stat className="tile" label="Unrealized P&L" value={<Delta value={totals.unrealized} kind="money" />} />
          <Stat
            className="tile"
            label="Shown"
            value={<span className="num">{rows.length}</span>}
            sub={`of ${all.length}`}
          />
        </section>
      )}

      {data && <AllocationPanel positions={rows} />}

      <Panel title="Book" meta="futures shown at notional; drift = weight minus target" flush>
        {error ? (
          <LoadError error={error} />
        ) : (
          <SortableTable
            data={rows}
            columns={columns}
            control="positions.table"
            rowKey={(p) => p.symbol}
            initialSort={[{ id: "market_value", desc: true }]}
            empty={all.length === 0 ? "No positions" : "No positions match the filters"}
            showFooter
            stickyFirst
          />
        )}
      </Panel>
    </div>
  );
}

/** Gross exposure by asset class as one bar of NAV, every class labelled with its share. */
function AllocationPanel({ positions }: { positions: Position[] }) {
  const parts = useMemo(() => {
    const by = new Map<Schemas["AssetClass"], number>();
    for (const p of positions) by.set(p.asset_class, (by.get(p.asset_class) ?? 0) + Math.abs(p.weight));
    return CLASS_ORDER.filter((c) => (by.get(c) ?? 0) > 0).map((c) => ({ id: c, value: by.get(c)! }));
  }, [positions]);
  const gross = parts.reduce((a, p) => a + p.value, 0);
  const scale = Math.max(1, gross);
  return (
    <Panel title="Allocation" meta={`gross ${pct(gross, 1)} of NAV, by asset class`}>
      <div className="alloc">
        <div
          className="alloc-bar"
          role="img"
          aria-label={`Gross exposure by asset class: ${parts.map((p) => `${ASSET_CLASS_LABEL[p.id]} ${pct(p.value, 1)}`).join(", ")}`}
        >
          {parts.map((p, i) => (
            <span
              key={p.id}
              className="alloc-seg"
              title={`${ASSET_CLASS_LABEL[p.id]}: ${pct(p.value, 1)} of NAV`}
              style={{
                width: `${(p.value / scale) * 100}%`,
                background: classColor(p.id),
                animationDelay: `${i * 60}ms`,
              }}
            />
          ))}
        </div>
        <ul className="alloc-legend">
          {parts.map((p) => (
            <li key={p.id}>
              <span className="legend-swatch" style={{ background: classColor(p.id) }} />
              {ASSET_CLASS_LABEL[p.id]} <span className="num">{pct(p.value, 1)}</span>
            </li>
          ))}
        </ul>
      </div>
    </Panel>
  );
}

function WeightCell({ weight, max }: { weight: number; max: number }) {
  return (
    <span className="weight-cell">
      <span
        className="weight-bar"
        data-side={weight < 0 ? "short" : "long"}
        style={{ width: `${(Math.abs(weight) / max) * 100}%` }}
      />
      <span className="weight-text">{pct(weight, 1)}</span>
    </span>
  );
}

function buildColumns(
  t: { mv: number; weight: number; dayPnl: number; unrealized: number },
  maxWeight: number,
): Column<Position>[] {
  // the figures read first sit next to the symbol; descriptive columns go last
  return [
    {
      id: "symbol",
      header: "Symbol",
      accessorKey: "symbol",
      cell: ({ row }) => (
        <span className="sym-cell">
          <span className="sym-dot" style={{ background: classColor(row.original.asset_class) }} aria-hidden="true" />
          <span className="sym">{row.original.symbol}</span> <span className="sub">{row.original.name}</span>
        </span>
      ),
      meta: { footer: "Total" },
    },
    {
      id: "market_value",
      header: "Mkt value",
      accessorFn: (p) => Math.abs(p.market_value),
      meta: { align: "right", footer: money(t.mv) },
      cell: ({ row }) => money(row.original.market_value),
    },
    {
      id: "weight",
      header: "Weight",
      accessorKey: "weight",
      meta: { align: "right", footer: pct(t.weight, 1) },
      cell: ({ row }) => <WeightCell weight={row.original.weight} max={maxWeight} />,
    },
    {
      id: "target",
      header: "Target",
      accessorFn: (p) => p.target_weight ?? 0,
      meta: { align: "right" },
      cell: ({ row }) => (row.original.target_weight == null ? "—" : pct(row.original.target_weight, 1)),
    },
    {
      id: "drift",
      header: "Drift",
      accessorFn: (p) => (p.target_weight == null ? 0 : p.weight - p.target_weight),
      meta: { align: "right" },
      cell: ({ row }) => {
        const p = row.original;
        if (p.target_weight == null) return "—";
        const d = p.weight - p.target_weight;
        return <span className={Math.abs(d) >= 0.03 ? "drift-flag num" : "num"}>{pct(d, 1, true)}</span>;
      },
    },
    {
      id: "day_return",
      header: "Day %",
      accessorKey: "day_return",
      meta: { align: "right" },
      cell: ({ row }) => <Delta value={row.original.day_return} chip />,
    },
    {
      id: "day_pnl",
      header: "Day P&L",
      accessorKey: "day_pnl",
      meta: { align: "right", footer: <Delta value={t.dayPnl} kind="money" /> },
      cell: ({ row }) => <Delta value={row.original.day_pnl} kind="money" />,
    },
    {
      id: "unrealized_pnl",
      header: "Unrealized",
      accessorKey: "unrealized_pnl",
      meta: { align: "right", footer: <Delta value={t.unrealized} kind="money" /> },
      cell: ({ row }) => <Delta value={row.original.unrealized_pnl} kind="money" />,
    },
    {
      id: "unrealized_return",
      header: "Unrl. %",
      accessorKey: "unrealized_return",
      meta: { align: "right" },
      cell: ({ row }) => <Delta value={row.original.unrealized_return} decimals={1} />,
    },
    {
      id: "quantity",
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
      id: "avg_cost",
      header: "Avg cost",
      accessorKey: "avg_cost",
      meta: { align: "right" },
      cell: ({ row }) => price(row.original.avg_cost),
    },
    { id: "sleeve", header: "Sleeve", accessorKey: "sleeve" },
    { id: "asset_class", header: "Class", accessorFn: (p) => ASSET_CLASS_LABEL[p.asset_class] },
  ];
}
