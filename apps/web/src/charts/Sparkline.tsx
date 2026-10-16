import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { line } from "d3-shape";
import { useWidth } from "./useSize";

/**
 * Trend for a headline figure: de-emphasised history in grey ink, the latest point in
 * the accent, over a hairline at the first value. It fills its container's width.
 */
export function Sparkline({ values, height = 64, label }: { values: number[]; height?: number; label: string }) {
  const [ref, width] = useWidth<HTMLDivElement>();
  // the box always renders, so its width is measured from the first paint
  if (values.length < 2) return <div ref={ref} className="sparkline-box" style={{ height }} />;
  const [lo = 0, hi = 1] = extent(values);
  const x = scaleLinear()
    .domain([0, values.length - 1])
    .range([2, Math.max(10, width - 5)]);
  const y = scaleLinear()
    .domain([lo, hi === lo ? lo + 1 : hi])
    .range([height - 4, 5]);
  const d = line<number>()
    .x((_, i) => x(i))
    .y((v) => y(v))(values);
  const last = values[values.length - 1]!;
  return (
    <div ref={ref} className="sparkline-box" style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={label} className="sparkline">
          <line x1={0} x2={width} y1={y(values[0]!)} y2={y(values[0]!)} className="sparkline-base" />
          <path d={d ?? ""} fill="none" stroke="var(--ink-2)" strokeWidth={1.5} strokeLinejoin="round" />
          <circle
            cx={x(values.length - 1)}
            cy={y(last)}
            r={3.5}
            fill="var(--accent)"
            stroke="var(--bg-page)"
            strokeWidth={1.5}
          />
        </svg>
      )}
    </div>
  );
}
