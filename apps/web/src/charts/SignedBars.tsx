import { scaleLinear } from "d3-scale";
import { useState } from "react";
import { useWidth } from "./useSize";

export interface SignedRow {
  id: string;
  label: string;
  value: number;
}

const ROW = 30;
const BAR = 14;
const LABEL_W = 150;
const VALUE_W = 76;

/** One signed value per row: gains right of zero (up colour), losses left (down colour). */
export function SignedBars({
  rows,
  format,
  ariaLabel,
  control,
}: {
  rows: SignedRow[];
  format: (v: number) => string;
  ariaLabel: string;
  control: string;
}) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const plotW = Math.max(40, width - LABEL_W - VALUE_W * 2);
  // Domain fits the data (always including zero), so one-sided data uses the full width.
  const lo = Math.min(0, ...rows.map((r) => r.value));
  const hi = Math.max(0, ...rows.map((r) => r.value));
  const span = Math.max(1e-9, hi - lo);
  const x = scaleLinear()
    .domain([lo === 0 ? -span * 0.02 : lo, hi === 0 ? span * 0.02 : hi])
    .range([0, plotW]);
  const height = rows.length * ROW + 4;

  return (
    <div ref={ref} className="chart">
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          data-control={control}
          onPointerLeave={() => setHover(null)}
        >
          <g transform={`translate(${LABEL_W + VALUE_W},2)`}>
            <line x1={x(0)} x2={x(0)} y1={0} y2={rows.length * ROW} className="axis-base" />
            {rows.map((r, i) => {
              const cy = i * ROW + ROW / 2;
              const w = Math.abs(x(r.value) - x(0));
              const pos = r.value >= 0;
              const x0 = pos ? x(0) : x(0) - w;
              const rr = Math.min(4, w, BAR / 2);
              const d = pos
                ? `M${x0},${cy - BAR / 2} H${x0 + w - rr} Q${x0 + w},${cy - BAR / 2} ${x0 + w},${cy - BAR / 2 + rr} V${cy + BAR / 2 - rr} Q${x0 + w},${cy + BAR / 2} ${x0 + w - rr},${cy + BAR / 2} H${x0} Z`
                : `M${x0 + w},${cy - BAR / 2} H${x0 + rr} Q${x0},${cy - BAR / 2} ${x0},${cy - BAR / 2 + rr} V${cy + BAR / 2 - rr} Q${x0},${cy + BAR / 2} ${x0 + rr},${cy + BAR / 2} H${x0 + w} Z`;
              return (
                <g
                  key={r.id}
                  className={hover === r.id ? "bar-row is-hover" : "bar-row"}
                  onPointerEnter={() => setHover(r.id)}
                  onFocus={() => setHover(r.id)}
                  onBlur={() => setHover(null)}
                  tabIndex={0}
                  aria-label={`${r.label}: ${format(r.value)}`}
                >
                  <rect x={-LABEL_W - VALUE_W} y={i * ROW} width={width} height={ROW} className="bar-hit" />
                  <text x={-LABEL_W - VALUE_W} y={cy + 4} className="bar-label">
                    {r.label}
                  </text>
                  {w > 0.5 && <path d={d} fill={pos ? "var(--up)" : "var(--down)"} />}
                  <text
                    x={pos ? x(r.value) + 6 : x(r.value) - 6}
                    y={cy + 4}
                    textAnchor={pos ? "start" : "end"}
                    className="bar-value num"
                  >
                    {format(r.value)}
                  </text>
                </g>
              );
            })}
          </g>
        </svg>
      )}
    </div>
  );
}
