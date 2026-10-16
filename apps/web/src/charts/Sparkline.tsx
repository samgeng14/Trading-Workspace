import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { area, line } from "d3-shape";
import { useId } from "react";
import { useWidth } from "./useSize";

/**
 * Trend for a headline figure: the line in the accent over a soft wash, the latest point lit.
 * It fills its container's width.
 */
export function Sparkline({ values, height = 64, label }: { values: number[]; height?: number; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const id = useId().replace(/[^a-zA-Z0-9-]/g, "");
  // the box always renders, so its width is measured from the first paint
  if (values.length < 2) return <div ref={ref} className="chart sparkline-box" style={{ height }} />;
  const [lo = 0, hi = 1] = extent(values);
  const x = scaleLinear()
    .domain([0, values.length - 1])
    .range([3, Math.max(10, width - 6)]);
  const y = scaleLinear()
    .domain([lo, hi === lo ? lo + 1 : hi])
    .range([height - 4, 6]);
  const d = line<number>()
    .x((_, i) => x(i))
    .y((v) => y(v))(values);
  const wash = area<number>()
    .x((_, i) => x(i))
    .y0(height)
    .y1((v) => y(v))(values);
  const last = values[values.length - 1]!;
  return (
    <div ref={ref} className="chart sparkline-box" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label} className="sparkline">
          <defs>
            <linearGradient id={`${id}-wash`} x1="0" x2="0" y1="0" y2="1">
              <stop offset="0" style={{ stopColor: "var(--accent)", stopOpacity: 0.3 }} />
              <stop offset="1" style={{ stopColor: "var(--accent)", stopOpacity: 0 }} />
            </linearGradient>
            <linearGradient id={`${id}-line`} x1="0" x2="1" y1="0" y2="0">
              <stop offset="0" style={{ stopColor: "var(--accent-2)", stopOpacity: 0.7 }} />
              <stop offset="1" style={{ stopColor: "var(--accent)" }} />
            </linearGradient>
          </defs>
          <path d={wash ?? ""} fill={`url(#${id}-wash)`} className="area-wash" />
          <path
            d={d ?? ""}
            fill="none"
            stroke={`url(#${id}-line)`}
            strokeWidth={2}
            strokeLinejoin="round"
            strokeLinecap="round"
            pathLength={1}
            className="series-line"
          />
          <circle cx={x(values.length - 1)} cy={y(last)} r={4} fill="var(--accent)" className="end-pulse" />
          <circle
            cx={x(values.length - 1)}
            cy={y(last)}
            r={3.5}
            fill="var(--accent)"
            stroke="var(--bg-panel)"
            strokeWidth={1.5}
          />
        </svg>
      )}
    </div>
  );
}
