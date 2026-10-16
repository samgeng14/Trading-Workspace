import { scaleLinear } from "d3-scale";
import { useState } from "react";
import { useWidth } from "./useSize";

export interface SignedRow {
  id: string;
  label: string;
  value: number;
}

const ROW = 32;
const BAR = 12;
const LABEL_WIDE = 150;
const VALUE_WIDE = 76;

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
  const gid = control.replace(/[^a-zA-Z0-9-]/g, "-");
  // a phone-width panel gives the bars the room the labels can spare
  const narrow = width > 0 && width < 480;
  const LABEL_W = narrow ? 112 : LABEL_WIDE;
  const VALUE_W = narrow ? 56 : VALUE_WIDE;
  // value labels sit at the bar ends: a side with no bars needs no room for them
  const leftV = rows.some((r) => r.value < 0) ? VALUE_W : 8;
  const rightV = rows.some((r) => r.value > 0) ? VALUE_W : 8;
  const plotW = Math.max(40, width - LABEL_W - leftV - rightV);
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
          <defs>
            {/* each bar brightens toward its end, away from zero */}
            <linearGradient id={`${gid}-up`} x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" style={{ stopColor: "var(--up)", stopOpacity: 0.5 }} />
              <stop offset="1" style={{ stopColor: "var(--up)", stopOpacity: 1 }} />
            </linearGradient>
            <linearGradient id={`${gid}-down`} x1="1" x2="0" y1="0" y2="0">
              <stop offset="0" style={{ stopColor: "var(--down)", stopOpacity: 0.5 }} />
              <stop offset="1" style={{ stopColor: "var(--down)", stopOpacity: 1 }} />
            </linearGradient>
          </defs>
          <g transform={`translate(${LABEL_W + leftV},2)`}>
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
                  <rect x={-LABEL_W - leftV} y={i * ROW} width={width} height={ROW} className="bar-hit" />
                  <text x={-LABEL_W - leftV} y={cy + 4} className="bar-label">
                    {r.label}
                  </text>
                  {w > 0.5 && (
                    <path
                      d={d}
                      fill={`url(#${gid}-${pos ? "up" : "down"})`}
                      className={`bar ${pos ? "from-left" : "from-right"}`}
                      style={{ animationDelay: `${i * 40}ms` }}
                    />
                  )}
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
