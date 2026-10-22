import {
  type ColumnDef,
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  type SortingState,
  useReactTable,
} from "@tanstack/react-table";
import { type ReactNode, useState } from "react";
import { SortIcon } from "./icons";

export type Column<T> = ColumnDef<T, unknown> & {
  meta?: { align?: "right"; footer?: ReactNode };
};

interface Props<T> {
  data: T[];
  columns: Column<T>[];
  initialSort?: SortingState;
  control: string;
  empty?: string;
  rowKey: (row: T) => string;
  showFooter?: boolean;
  maxHeight?: number | string;
}

/** Dense, sortable table. Headers are buttons (keyboard-sortable); numbers are right-aligned. */
export function SortableTable<T>({
  data,
  columns,
  initialSort = [],
  control,
  empty = "Nothing to show",
  rowKey,
  showFooter,
  maxHeight,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>(initialSort);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    enableSortingRemoval: false, // a header click flips asc/desc; it never leaves the table unsorted
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: rowKey,
  });

  return (
    <div className="table-wrap" style={{ maxHeight }}>
      <table className="table">
        <thead>
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((h) => {
                const meta = (h.column.columnDef as Column<T>).meta;
                const sorted = h.column.getIsSorted();
                return (
                  <th
                    key={h.id}
                    className={meta?.align === "right" ? "r" : ""}
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : "none"}
                  >
                    {h.column.getCanSort() ? (
                      <button
                        type="button"
                        className="th-sort"
                        data-active={sorted !== false}
                        onClick={h.column.getToggleSortingHandler()}
                        data-control={`${control}.sort.${h.column.id}`}
                      >
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        <SortIcon dir={sorted} />
                      </button>
                    ) : (
                      flexRender(h.column.columnDef.header, h.getContext())
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {table.getRowModel().rows.map((row) => (
            <tr key={row.id}>
              {row.getVisibleCells().map((cell) => {
                const meta = (cell.column.columnDef as Column<T>).meta;
                return (
                  <td key={cell.id} className={meta?.align === "right" ? "r" : ""}>
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                );
              })}
            </tr>
          ))}
          {data.length === 0 && (
            <tr>
              <td colSpan={columns.length} className="empty">
                {empty}
              </td>
            </tr>
          )}
        </tbody>
        {showFooter && data.length > 0 && (
          <tfoot>
            <tr>
              {table.getVisibleLeafColumns().map((col) => {
                const meta = (col.columnDef as Column<T>).meta;
                return (
                  <td key={col.id} className={meta?.align === "right" ? "r num" : ""}>
                    {meta?.footer}
                  </td>
                );
              })}
            </tr>
          </tfoot>
        )}
      </table>
    </div>
  );
}
