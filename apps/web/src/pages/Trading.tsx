import { useMemo, useState } from "react";
import { useCancelOrder, useOrders, useRebalancePreview, useSubmitRebalance } from "../api/hooks";
import type { Schemas } from "../api/client";
import { LoadError, PageHead, Panel, Segmented, Stat, StatusBadge } from "../components/bits";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { type Column, SortableTable } from "../components/SortableTable";
import { useToast } from "../components/Toasts";
import { dateLong, dateShort, money, num, pct, price, shortTimeET } from "../lib/format";

type Order = Schemas["Order"];
type PreviewRow = Schemas["RebalancePreview"]["rows"][number];

export function Trading() {
  return (
    <div className="page">
      <PageHead title="Trading" sub="Rebalance preview, submission and today's blotter" />
      <RebalancePanel />
      <BlotterPanel />
    </div>
  );
}

// ── rebalance ──────────────────────────────────────────────────────────

const ROW_VIEWS = [
  { value: "trades", label: "Trades only" },
  { value: "all", label: "All names" },
] as const;

function RebalancePanel() {
  const { data, error } = useRebalancePreview();
  const submit = useSubmitRebalance();
  const toast = useToast();
  const [rowsView, setRowsView] = useState<"trades" | "all">("trades");
  const [confirming, setConfirming] = useState(false);

  const rows = useMemo(() => {
    const all = data?.rows ?? [];
    const shown = rowsView === "trades" ? all.filter((r) => r.trade_qty !== 0) : all;
    return shown;
  }, [data, rowsView]);

  const blocker = data?.checks.find((c) => c.status === "fail");
  const columns = useMemo(() => previewColumns(), []);

  return (
    <Panel
      title="Rebalance preview"
      meta={
        data ? `${data.sleeve} · targets from ${dateShort(data.targets_date)} close · ${data.execution}` : undefined
      }
      actions={
        <Segmented
          value={rowsView}
          options={ROW_VIEWS}
          onChange={setRowsView}
          label="Rows"
          control="trading.preview.rows"
        />
      }
      flush
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        data && (
          <>
            <div className="preview-head">
              <ul className="checks" aria-label="Pre-trade checks">
                {data.checks.map((c) => (
                  <li key={c.id}>
                    <StatusBadge status={c.status} label={c.label} />
                    <span className="muted">{c.detail}</span>
                  </li>
                ))}
              </ul>
              <div className="preview-totals">
                <Stat label="Orders" value={<span className="num">{data.totals.orders}</span>} />
                <Stat
                  label="Buy"
                  value={<span className="num">{money(data.totals.buy_notional, { compact: true })}</span>}
                />
                <Stat
                  label="Sell"
                  value={<span className="num">{money(data.totals.sell_notional, { compact: true })}</span>}
                />
                <Stat
                  label="Turnover"
                  value={<span className="num">{pct(data.totals.turnover, 1)}</span>}
                  sub="of NAV"
                />
                <Stat
                  label="Est. cost"
                  value={<span className="num">{money(data.totals.est_cost)}</span>}
                  sub="estimated"
                />
                <div className="preview-submit">
                  <button
                    type="button"
                    className="btn btn-primary"
                    disabled={!data.can_submit}
                    onClick={() => setConfirming(true)}
                    data-control="trading.preview.submit"
                  >
                    Submit {data.totals.orders} orders
                  </button>
                  <span className="stat-sub">
                    {data.can_submit
                      ? "Requires typed confirmation"
                      : blocker
                        ? `Blocked: ${blocker.label.toLowerCase()}`
                        : "Nothing to trade"}
                  </span>
                </div>
              </div>
            </div>
            <SortableTable
              data={rows}
              columns={columns}
              control="trading.preview.table"
              rowKey={(r) => r.symbol}
              initialSort={[{ id: "est_notional", desc: true }]}
              empty="Book is on target: nothing to trade"
            />
          </>
        )
      )}
      {data && (
        <ConfirmDialog
          open={confirming}
          onOpenChange={setConfirming}
          title={`Submit ${data.totals.orders} orders?`}
          confirmLabel="Submit orders"
          requireText="SUBMIT"
          control="rebalance.submit"
          onConfirm={async () => {
            const result = await submit.mutateAsync();
            toast(result.message, "success");
          }}
        >
          <p>
            {data.sleeve}: buy {money(data.totals.buy_notional)}, sell {money(data.totals.sell_notional)}. Orders go to
            the paper account as {data.execution}.
          </p>
        </ConfirmDialog>
      )}
    </Panel>
  );
}

function previewColumns(): Column<PreviewRow>[] {
  return [
    {
      id: "symbol",
      header: "Symbol",
      accessorKey: "symbol",
      cell: ({ row }) => <span className="sym">{row.original.symbol}</span>,
    },
    {
      id: "current_weight",
      header: "Current",
      accessorKey: "current_weight",
      meta: { align: "right" },
      cell: ({ row }) => pct(row.original.current_weight, 1),
    },
    {
      id: "target_weight",
      header: "Target",
      accessorKey: "target_weight",
      meta: { align: "right" },
      cell: ({ row }) => pct(row.original.target_weight, 1),
    },
    {
      id: "change",
      header: "Change",
      accessorFn: (r) => r.target_weight - r.current_weight,
      meta: { align: "right" },
      cell: ({ row }) => pct(row.original.target_weight - row.original.current_weight, 1, true),
    },
    {
      id: "current_qty",
      header: "Held",
      accessorKey: "current_qty",
      meta: { align: "right" },
      cell: ({ row }) => num(row.original.current_qty),
    },
    {
      id: "trade_qty",
      header: "Trade",
      accessorKey: "trade_qty",
      meta: { align: "right" },
      cell: ({ row }) => {
        const q = row.original.trade_qty;
        if (q === 0) return <span className="muted">&mdash;</span>;
        return (
          <span className="num">
            <span className={q > 0 ? "side-buy" : "side-sell"}>{q > 0 ? "BUY" : "SELL"}</span> {num(Math.abs(q))}
          </span>
        );
      },
    },
    {
      id: "est_notional",
      header: "Notional",
      accessorFn: (r) => Math.abs(r.est_notional),
      meta: { align: "right" },
      cell: ({ row }) => (row.original.est_notional === 0 ? "—" : money(row.original.est_notional, { sign: true })),
    },
    {
      id: "est_cost",
      header: "Est. cost",
      accessorKey: "est_cost",
      meta: { align: "right" },
      cell: ({ row }) => (row.original.est_cost ? money(row.original.est_cost, { decimals: 2 }) : "—"),
    },
  ];
}

// ── blotter ────────────────────────────────────────────────────────────

type Filter = "all" | "working" | "filled" | "cancelled";
const FILTERS = [
  { value: "all", label: "All" },
  { value: "working", label: "Working" },
  { value: "filled", label: "Filled" },
  { value: "cancelled", label: "Cancelled" },
] as const;

const WORKING = new Set<Order["status"]>(["pending", "submitted", "partial"]);

function BlotterPanel() {
  const { data, error } = useOrders();
  const cancel = useCancelOrder();
  const toast = useToast();
  const [filter, setFilter] = useState<Filter>("all");

  const orders = useMemo(() => {
    const all = data?.orders ?? [];
    if (filter === "working") return all.filter((o) => WORKING.has(o.status));
    if (filter === "filled") return all.filter((o) => o.status === "filled");
    if (filter === "cancelled") return all.filter((o) => o.status === "cancelled" || o.status === "rejected");
    return all;
  }, [data, filter]);

  const filled = (data?.orders ?? []).filter((o) => o.status === "filled" && o.avg_fill_price !== undefined);
  const filledNotional = filled.reduce((a, o) => a + o.quantity * (o.avg_fill_price ?? 0), 0);
  const avgSlip =
    filledNotional > 0
      ? filled.reduce((a, o) => a + (o.slippage_bps ?? 0) * o.quantity * (o.avg_fill_price ?? 0), 0) / filledNotional
      : null;

  const columns = useMemo<Column<Order>[]>(
    () => [
      {
        id: "submitted_at",
        header: "Time",
        accessorKey: "submitted_at",
        cell: ({ row }) => <span className="num">{shortTimeET(row.original.submitted_at)}</span>,
      },
      {
        id: "symbol",
        header: "Symbol",
        accessorKey: "symbol",
        cell: ({ row }) => <span className="sym">{row.original.symbol}</span>,
      },
      {
        id: "side",
        header: "Side",
        accessorKey: "side",
        cell: ({ row }) => (
          <span className={row.original.side === "BUY" ? "side-buy" : "side-sell"}>{row.original.side}</span>
        ),
      },
      {
        id: "quantity",
        header: "Qty",
        accessorKey: "quantity",
        meta: { align: "right" },
        cell: ({ row }) => num(row.original.quantity),
      },
      { id: "order_type", header: "Type", accessorKey: "order_type" },
      {
        id: "status",
        header: "Status",
        accessorKey: "status",
        cell: ({ row }) => <OrderStatus status={row.original.status} />,
      },
      {
        id: "filled",
        header: "Filled",
        accessorKey: "filled",
        meta: { align: "right" },
        cell: ({ row }) => num(row.original.filled),
      },
      {
        id: "arrival",
        header: "Arrival",
        accessorFn: (o) => o.arrival_price ?? 0,
        meta: { align: "right" },
        cell: ({ row }) => price(row.original.arrival_price),
      },
      {
        id: "avg_fill",
        header: "Avg fill",
        accessorFn: (o) => o.avg_fill_price ?? 0,
        meta: { align: "right" },
        cell: ({ row }) => price(row.original.avg_fill_price),
      },
      {
        id: "slippage",
        header: "Slippage",
        accessorFn: (o) => o.slippage_bps ?? 0,
        meta: { align: "right" },
        cell: ({ row }) =>
          row.original.slippage_bps === undefined ? (
            "—"
          ) : (
            <span className="num">{num(row.original.slippage_bps, 1, true)} bp</span>
          ),
      },
      { id: "sleeve", header: "Sleeve", accessorKey: "sleeve" },
      {
        id: "reason",
        header: "Reason",
        accessorFn: (o) => o.reason ?? "",
        cell: ({ row }) => <span className="secondary">{row.original.reason}</span>,
      },
      {
        id: "action",
        header: "",
        enableSorting: false,
        cell: ({ row }) =>
          WORKING.has(row.original.status) ? (
            <button
              type="button"
              className="btn btn-danger"
              disabled={cancel.isPending}
              onClick={async () => {
                try {
                  await cancel.mutateAsync(row.original.id);
                  toast(
                    `Cancel sent for ${row.original.side} ${num(row.original.quantity)} ${row.original.symbol}`,
                    "info",
                  );
                } catch (e) {
                  toast(e instanceof Error ? e.message : String(e), "error");
                }
              }}
              data-control="trading.orders.cancel"
            >
              Cancel
            </button>
          ) : null,
      },
    ],
    [cancel, toast],
  );

  return (
    <Panel
      title="Orders"
      meta={data ? `${dateLong(data.trading_date)} · ${data.orders.length} orders` : undefined}
      actions={
        <Segmented
          value={filter}
          options={FILTERS}
          onChange={setFilter}
          label="Status"
          control="trading.orders.filter"
        />
      }
      flush
      foot={
        <span className="num">
          Filled {money(filledNotional, { compact: true })} · notional-weighted slippage vs arrival{" "}
          {avgSlip === null ? "—" : `${num(avgSlip, 1, true)} bp`} (positive = paid)
        </span>
      }
    >
      {error ? (
        <LoadError error={error} />
      ) : (
        <SortableTable
          data={orders}
          columns={columns}
          control="trading.orders.table"
          rowKey={(o) => o.id}
          initialSort={[{ id: "submitted_at", desc: true }]}
          empty={filter === "all" ? "No orders this session." : "No orders with this status"}
        />
      )}
    </Panel>
  );
}

function OrderStatus({ status }: { status: Order["status"] }) {
  if (status === "filled") return <StatusBadge status="ok" label="Filled" />;
  if (status === "rejected") return <StatusBadge status="error" label="Rejected" />;
  if (status === "cancelled") return <StatusBadge status="skip" label="Cancelled" />;
  if (status === "partial") return <StatusBadge status="warning" label="Partial" />;
  return <StatusBadge status="unknown" label={status === "pending" ? "Pending" : "Working"} />;
}
