import { type ReactNode, useState } from "react";

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

/** Panel holding a chart, with a Chart / Table switch (the table is the accessible twin). */
export function Figure({ title, meta, actions, chart, table, control, className = "", stale = false }: Props) {
  const [view, setView] = useState<"chart" | "table">("chart");
  return (
    <section className={`panel ${className}`} aria-label={title}>
      <header className="panel-head">
        <h2 className="panel-title">{title}</h2>
        {meta && <span className="panel-meta">{meta}</span>}
        <div className="panel-actions">
          {actions}
          <div className="segmented" role="group" aria-label={`${title} view`}>
            <button
              type="button"
              aria-pressed={view === "chart"}
              onClick={() => setView("chart")}
              data-control={`${control}.view-chart`}
            >
              Chart
            </button>
            <button
              type="button"
              aria-pressed={view === "table"}
              onClick={() => setView("table")}
              data-control={`${control}.view-table`}
            >
              Table
            </button>
          </div>
        </div>
      </header>
      <div className={`panel-body ${view === "table" ? "flush" : ""}`} style={{ opacity: stale ? 0.55 : 1 }}>
        {view === "chart" ? (
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
