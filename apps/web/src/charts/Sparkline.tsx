import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { line } from "d3-shape";

/** Trend line for a stat tile: de-emphasised history, the latest point in the accent. */
export function Sparkline({
  values,
  width = 120,
  height = 28,
  label,
}: {
  values: number[];
  width?: number;
  height?: number;
  label: string;
}) {
  if (values.length < 2) return null;
  const [lo = 0, hi = 1] = extent(values);
  const x = scaleLinear()
    .domain([0, values.length - 1])
    .range([2, width - 4]);
  const y = scaleLinear()
    .domain([lo, hi === lo ? lo + 1 : hi])
    .range([height - 3, 3]);
  const d = line<number>()
    .x((_, i) => x(i))
    .y((v) => y(v))(values);
  const last = values[values.length - 1]!;
  return (
    <svg width={width} height={height} role="img" aria-label={label} className="sparkline">
      <path d={d ?? ""} fill="none" stroke="var(--bench)" strokeWidth={1.5} strokeLinejoin="round" />
      <circle
        cx={x(values.length - 1)}
        cy={y(last)}
        r={3}
        fill="var(--accent)"
        stroke="var(--bg-panel)"
        strokeWidth={1.5}
      />
    </svg>
  );
}
