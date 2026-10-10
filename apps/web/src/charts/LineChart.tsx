import { extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { area as d3area, line as d3line } from "d3-shape";
import { useMemo, useState } from "react";
import { useWidth } from "./useSize";

export interface LineSeries {
  id: string;
  label: string;
  /** CSS colour, usually a token like var(--series-1). */
  color: string;
  values: (number | null)[];
  /** Soft 10% wash under the line (single-series charts). */
  area?: boolean;
  /** Text shown after the label at the line's end, e.g. "+9.8%". */
  endText?: string;
}

interface Props {
  labels: string[];
  series: LineSeries[];
  yFormat: (v: number) => string;
  xLabel: (i: number) => string;
  xTicks: { index: number; text: string }[];
  height?: number;
  baseline?: number;
  /** Fill below/above the baseline with the down/up washes (P&L curves). */
  signedFill?: boolean;
  /** Reserve the full x range even if values stop early (a live session). */
  xLength?: number;
  /** Upper bound for the y axis (0 for drawdowns). */
  yMax?: number;
  ariaLabel: string;
  control: string;
}

const M = { top: 10, bottom: 22, left: 0 };

export function LineChart({
  labels,
  series,
  yFormat,
  xLabel,
  xTicks,
  height = 240,
  baseline,
  signedFill = false,
  xLength,
  yMax,
  ariaLabel,
  control,
}: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const endLabels = series.some((s) => s.endText !== undefined);
  const right = endLabels ? 112 : 8;
  const n = xLength ?? labels.length;
  const innerW = Math.max(10, width - M.left - right);
  const innerH = height - M.top - M.bottom;

  const { x, y, yTicks } = useMemo(() => {
    const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
    if (baseline !== undefined) all.push(baseline);
    const [lo = 0, hi = 1] = extent(all);
    const pad = (hi - lo || Math.abs(hi) || 1) * 0.08;
    const top = yMax !== undefined ? Math.min(hi + pad, yMax) : hi + pad;
    const yScale = scaleLinear()
      .domain([lo - pad, top])
      .nice(5)
      .range([innerH, 0]);
    if (yMax !== undefined) yScale.domain([yScale.domain()[0]!, Math.min(yScale.domain()[1]!, yMax)]);
    const xScale = scaleLinear()
      .domain([0, Math.max(1, n - 1)])
      .range([0, innerW]);
    return { x: xScale, y: yScale, yTicks: yScale.ticks(5) };
  }, [series, baseline, innerH, innerW, n, yMax]);

  const paths = useMemo(
    () =>
      series.map((s) => {
        const pts = s.values.map((v, i) => [i, v] as const);
        const line = d3line<readonly [number, number | null]>()
          .defined((d) => d[1] !== null)
          .x((d) => x(d[0]))
          .y((d) => y(d[1]!));
        const fillBase = baseline ?? y.domain()[0]!;
        const area = d3area<readonly [number, number | null]>()
          .defined((d) => d[1] !== null)
          .x((d) => x(d[0]))
          .y0(y(fillBase))
          .y1((d) => y(d[1]!));
        return { s, line: line(pts) ?? "", area: area(pts) ?? "" };
      }),
    [series, x, y, baseline],
  );

  const lastIndex = (s: LineSeries) => {
    for (let i = s.values.length - 1; i >= 0; i--) if (s.values[i] !== null) return i;
    return -1;
  };

  // End labels: sorted by y, pushed apart when they collide, with a leader line.
  const ends = useMemo(() => {
    if (!endLabels) return [];
    const items = series
      .map((s) => {
        const i = lastIndex(s);
        return i < 0 ? null : { s, i, py: y(s.values[i]!), ly: y(s.values[i]!) };
      })
      .filter((d): d is NonNullable<typeof d> => d !== null)
      .sort((a, b) => a.py - b.py);
    for (let k = 1; k < items.length; k++) {
      const prev = items[k - 1]!;
      const cur = items[k]!;
      if (cur.ly - prev.ly < 28) cur.ly = prev.ly + 28;
    }
    return items;
  }, [endLabels, series, y]);

  const move = (clientX: number, rect: DOMRect) => {
    const i = Math.round(x.invert(clientX - rect.left - M.left));
    const maxI = Math.max(...series.map(lastIndex));
    setHover(Math.min(Math.max(0, i), maxI));
  };

  const onKey = (e: React.KeyboardEvent) => {
    const maxI = Math.max(...series.map(lastIndex));
    if (e.key === "ArrowLeft") setHover((h) => Math.max(0, (h ?? maxI) - 1));
    else if (e.key === "ArrowRight") setHover((h) => Math.min(maxI, (h ?? maxI - 1) + 1));
    else if (e.key === "Home") setHover(0);
    else if (e.key === "End") setHover(maxI);
    else if (e.key === "Escape") setHover(null);
    else return;
    e.preventDefault();
  };

  const zeroY = baseline !== undefined ? y(baseline) : null;
  const clipId = control.replace(/[^a-zA-Z0-9-]/g, "-");
  const hx = hover !== null ? x(hover) : 0;
  const tipLeft = hx + M.left > width / 2;

  return (
    <div ref={ref} className="chart" style={{ height }}>
      {width > 0 && (
        <svg
          width={width}
          height={height}
          role="img"
          aria-label={ariaLabel}
          tabIndex={0}
          data-control={control}
          onKeyDown={onKey}
          onBlur={() => setHover(null)}
          onPointerMove={(e) => move(e.clientX, e.currentTarget.getBoundingClientRect())}
          onPointerLeave={() => setHover(null)}
        >
          {signedFill && zeroY !== null && (
            <defs>
              <clipPath id={`${clipId}-above`}>
                <rect x={0} y={0} width={width} height={M.top + zeroY} />
              </clipPath>
              <clipPath id={`${clipId}-below`}>
                <rect x={0} y={M.top + zeroY} width={width} height={height} />
              </clipPath>
            </defs>
          )}
          <g transform={`translate(${M.left},${M.top})`}>
            {yTicks.map((t) => (
              <g key={t} transform={`translate(0,${y(t)})`}>
                <line x1={0} x2={innerW} className={t === baseline ? "axis-base" : "grid"} />
                <text x={0} y={-4} className="tick num">
                  {yFormat(t)}
                </text>
              </g>
            ))}
            {zeroY !== null && !yTicks.includes(baseline!) && (
              <line x1={0} x2={innerW} y1={zeroY} y2={zeroY} className="axis-base" />
            )}
            {xTicks.map((t) => (
              <g key={t.index} transform={`translate(${x(t.index)},${innerH})`}>
                <line y1={0} y2={4} className="axis-base" />
                <text
                  y={16}
                  textAnchor={x(t.index) < 24 ? "start" : x(t.index) > innerW - 24 ? "end" : "middle"}
                  className="tick num"
                >
                  {t.text}
                </text>
              </g>
            ))}
            {paths.map(({ s, area }) =>
              signedFill && zeroY !== null ? (
                <g key={`a-${s.id}`}>
                  <path d={area} className="fill-up" clipPath={`url(#${clipId}-above)`} />
                  <path d={area} className="fill-down" clipPath={`url(#${clipId}-below)`} />
                </g>
              ) : s.area ? (
                <path key={`a-${s.id}`} d={area} fill={s.color} fillOpacity={0.1} />
              ) : null,
            )}
            {paths.map(({ s, line }) => (
              <path
                key={`l-${s.id}`}
                d={line}
                fill="none"
                stroke={s.color}
                strokeWidth={2}
                strokeLinejoin="round"
                strokeLinecap="round"
              />
            ))}
            {ends.map(({ s, i, py, ly }) => (
              <g key={`e-${s.id}`}>
                <circle cx={x(i)} cy={py} r={4} fill={s.color} stroke="var(--bg-panel)" strokeWidth={2} />
                {Math.abs(ly - py) > 1 && <line x1={x(i) + 6} y1={py} x2={innerW + 8} y2={ly} className="leader" />}
                <text x={innerW + 10} y={ly - 2} className="end-label">
                  {s.label}
                </text>
                <text x={innerW + 10} y={ly + 11} className="end-value num">
                  {s.endText}
                </text>
              </g>
            ))}
            {hover !== null && (
              <g>
                <line x1={hx} x2={hx} y1={0} y2={innerH} className="crosshair" />
                {series.map((s) =>
                  s.values[hover] !== null && s.values[hover] !== undefined ? (
                    <circle
                      key={s.id}
                      cx={hx}
                      cy={y(s.values[hover]!)}
                      r={4}
                      fill={s.color}
                      stroke="var(--bg-panel)"
                      strokeWidth={2}
                    />
                  ) : null,
                )}
              </g>
            )}
          </g>
        </svg>
      )}
      {hover !== null && (
        <div className="tooltip" style={tipLeft ? { right: width - (hx + M.left) + 12 } : { left: hx + M.left + 12 }}>
          <div className="tooltip-x num">{xLabel(hover)}</div>
          {series.map((s) =>
            s.values[hover] !== null && s.values[hover] !== undefined ? (
              <div key={s.id} className="tooltip-row">
                <span className="tooltip-key" style={{ background: s.color }} />
                <span className="tooltip-value num">{yFormat(s.values[hover]!)}</span>
                <span className="tooltip-label">{s.label}</span>
              </div>
            ) : null,
          )}
        </div>
      )}
    </div>
  );
}

/** Month-start ticks for a list of YYYY-MM-DD dates, thinned to fit. */
export function monthTicks(dates: string[], maxTicks = 8): { index: number; text: string }[] {
  const out: { index: number; text: string }[] = [];
  let prev = "";
  dates.forEach((d, i) => {
    const m = d.slice(0, 7);
    if (m !== prev) {
      if (prev !== "" || i === 0) out.push({ index: i, text: monthTick(d, i === 0 || m.endsWith("-01")) });
      prev = m;
    }
  });
  const step = Math.ceil(out.length / maxTicks);
  return out.filter((_, k) => k % step === 0);
}

function monthTick(date: string, withYear: boolean): string {
  const dt = new Date(date + "T00:00:00Z");
  const mon = dt.toLocaleString("en-US", { month: "short", timeZone: "UTC" });
  return withYear ? `${mon} ’${String(dt.getUTCFullYear()).slice(2)}` : mon;
}
