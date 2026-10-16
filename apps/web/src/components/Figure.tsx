import { type ReactNode, useState } from "react";
import { Segmented } from "./bits";

export interface TableView {
  head: string[];
  rows: (string | number)[][];
  /** Right-align these column indexes (numbers). */
  numeric?: number[];
}

interface Props {
  title: string;
  meta?: ReactNode;
  actions?: ReactNode;
  chart: ReactNode;
  table: TableView;
  control: string;
  className?: string;
  /** Dim the frame while new data loads instead of flashing a skeleton. */
  stale?: boolean;
}

const VIEWS = [
  { value: "view-chart", label: "Chart" },
  { value: "view-table", label: "Table" },
] as const;

/** Panel holding a chart, with a Chart / Table switch (the table is the accessible twin). */
export function Figure({ title, meta, actions, chart, table, control, className = "", stale = false }: Props) {
  const [view, setView] = useState<"view-chart" | "view-table">("view-chart");
  return (
    <section className={`panel figure ${className}`} aria-label={title}>
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
        {meta && <span className="panel-meta">{meta}</span>}
        <div className="panel-actions">
          {actions}
          <Segmented value={view} options={VIEWS} onChange={setView} label={`${title} view`} control={control} />
        </div>
      </header>
      <div
        className={`panel-body ${view === "view-table" ? "flush" : ""}`}
        style={{ opacity: stale ? 0.55 : 1, transition: "opacity 0.25s" }}
      >
        {view === "view-chart" ? (
          chart
        ) : (
          <div className="table-wrap figure-table">
            <table className="table">
              <thead>
                <tr>
                  {table.head.map((h, i) => (
                    <th key={h} className={table.numeric?.includes(i) ? "r" : ""}>
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {table.rows.map((row, r) => (
                  <tr key={r}>
                    {row.map((cell, c) => (
                      <td key={c} className={table.numeric?.includes(c) ? "r" : ""}>
                        {cell}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}
