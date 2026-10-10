import { scaleLinear } from "d3-scale";
import { useState } from "react";
import { useWidth } from "./useSize";

export interface DivergingRow {
  id: string;
  label: string;
  long: number; // >= 0
  short: number; // <= 0
  net: number;
}

interface Props {
  rows: DivergingRow[];
  format: (v: number) => string;
  ariaLabel: string;
  control: string;
}

const ROW = 30;
const BAR = 14;
const LABEL_W = 104;
const VALUE_W = 64;

/**
 * Long exposure to the right of zero, short to the left, net as a tick.
 * Two series, so a legend sits above; values are printed at the bar ends.
 */
export function DivergingBars({ rows, format, ariaLabel, control }: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<string | null>(null);
  const plotW = Math.max(40, width - LABEL_W - VALUE_W * 2);
  // Fit the data: the long side usually dominates, so don't reserve equal room for shorts.
  const hi = Math.max(0.05, ...rows.map((r) => r.long));
  const lo = Math.min(-0.02, ...rows.map((r) => r.short));
  const x = scaleLinear().domain([lo, hi]).range([0, plotW]);
  const height = rows.length * ROW + 8;
  const hovered = rows.find((r) => r.id === hover);

  return (
    <div ref={ref} className="chart">
      <div className="legend">
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "var(--series-1)" }} />
          Long
        </span>
        <span className="legend-item">
          <span className="legend-swatch" style={{ background: "var(--series-2)" }} />
          Short
        </span>
        <span className="legend-item">
          <span className="legend-tick" />
          Net
        </span>
      </div>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          data-control={control}
          onPointerLeave={() => setHover(null)}
        >
          <g transform={`translate(${LABEL_W + VALUE_W},4)`}>
            <line x1={x(0)} x2={x(0)} y1={0} y2={rows.length * ROW} className="axis-base" />
            {rows.map((r, i) => {
              const cy = i * ROW + ROW / 2;
              const longW = x(r.long) - x(0);
              const shortW = x(0) - x(r.short);
              return (
                <g
                  key={r.id}
                  onPointerEnter={() => setHover(r.id)}
                  onFocus={() => setHover(r.id)}
                  onBlur={() => setHover(null)}
                  tabIndex={0}
                  aria-label={`${r.label}: long ${format(r.long)}, short ${format(r.short)}, net ${format(r.net)}`}
                  className={hover === r.id ? "bar-row is-hover" : "bar-row"}
                >
                  <rect x={-LABEL_W - VALUE_W} y={i * ROW} width={width} height={ROW} className="bar-hit" />
                  <text x={-LABEL_W - VALUE_W} y={cy + 4} className="bar-label">
                    {r.label}
                  </text>
                  {r.long > 0 && <path d={roundedRight(x(0), cy - BAR / 2, longW, BAR)} fill="var(--series-1)" />}
                  {r.short < 0 && (
                    <path d={roundedLeft(x(0) - shortW, cy - BAR / 2, shortW, BAR)} fill="var(--series-2)" />
                  )}
                  <line x1={x(r.net)} x2={x(r.net)} y1={cy - BAR / 2 - 3} y2={cy + BAR / 2 + 3} className="net-tick" />
                  {r.long > 0 && (
                    <text x={x(r.long) + 6} y={cy + 4} className="bar-value num">
                      {format(r.long)}
                    </text>
                  )}
                  {r.short < 0 && (
                    <text x={x(r.short) - 6} y={cy + 4} textAnchor="end" className="bar-value num">
                      {format(r.short)}
                    </text>
                  )}
                </g>
              );
            })}
          </g>
        </svg>
      )}
      {hovered && (
        <div className="tooltip" style={{ right: 8, top: 28 }}>
          <div className="tooltip-x">{hovered.label}</div>
          <div className="tooltip-row">
            <span className="tooltip-key" style={{ background: "var(--series-1)" }} />
            <span className="tooltip-value num">{format(hovered.long)}</span>
            <span className="tooltip-label">Long</span>
          </div>
          <div className="tooltip-row">
            <span className="tooltip-key" style={{ background: "var(--series-2)" }} />
            <span className="tooltip-value num">{format(hovered.short)}</span>
            <span className="tooltip-label">Short</span>
          </div>
          <div className="tooltip-row">
            <span className="tooltip-key" style={{ background: "var(--ink-1)" }} />
            <span className="tooltip-value num">{format(hovered.net)}</span>
            <span className="tooltip-label">Net</span>
          </div>
        </div>
      )}
    </div>
  );
}

// Bars are square at the zero baseline and rounded (4px) at the data end.
function roundedRight(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w, h / 2);
  return `M${x},${y} H${x + w - r} Q${x + w},${y} ${x + w},${y + r} V${y + h - r} Q${x + w},${y + h} ${x + w - r},${y + h} H${x} Z`;
}

function roundedLeft(x: number, y: number, w: number, h: number): string {
  const r = Math.min(4, w, h / 2);
  return `M${x + w},${y} H${x + r} Q${x},${y} ${x},${y + r} V${y + h - r} Q${x},${y + h} ${x + r},${y + h} H${x + w} Z`;
}
