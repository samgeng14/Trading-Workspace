import { scaleLinear } from "d3-scale";
import { area as d3area, line as d3line } from "d3-shape";
import { useMemo, useState } from "react";
import { fitXTicks, nearestIndex, xAnchor, type XTick, Y_LABEL_GAP, yGutter, yScale } from "./axis";
import { useWidth } from "./useSize";

export interface LineSeries {
  id: string;
  label: string;
  /** Legend text when the label alone isn't enough, e.g. how a benchmark line was scaled. */
  legend?: string;
  /** CSS colour, usually a token like var(--series-1). */
  color: string;
  values: (number | null)[];
  /** Soft 10% wash under the line (single-series charts). */
  area?: boolean;
  /** Text shown after the label at the line's end, e.g. "+9.8%". */
  endText?: string;
}

interface Props {
  series: LineSeries[];
  /** Tick labels. Gets every tick, so the axis can share one unit and precision. */
  yFormat: (v: number, ticks: readonly number[]) => string;
  /** Tooltip values; defaults to the tick format. */
  tipFormat?: (v: number) => string;
  xLabel: (i: number) => string;
  xTicks: XTick[];
  /** Each point's x and the full x range (e.g. minutes into a session). Defaults to the point's index. */
  x?: { values: number[]; domain: [number, number] };
  height?: number;
  baseline?: number;
  /** Fill below/above the baseline with the down/up washes (P&L curves). */
  signedFill?: boolean;
  /** Upper bound for the y axis (0 for drawdowns). */
  yMax?: number;
  ariaLabel: string;
  control: string;
}

const M = { top: 10, bottom: 22 };

export function LineChart({
  series,
  yFormat,
  tipFormat,
  xLabel,
  xTicks,
  x: xAt,
  height = 240,
  baseline,
  signedFill = false,
  yMax,
  ariaLabel,
  control,
}: Props) {
  const [ref, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);
  const endLabels = series.some((s) => s.endText !== undefined);
  const right = endLabels ? 112 : 8;
  const n = Math.max(0, ...series.map((s) => s.values.length));
  const innerH = height - M.top - M.bottom;

  // The y labels get a gutter of their own, sized to the widest one, so they never sit on the plot.
  const { y, yTicks, yLabels, left } = useMemo(() => {
    const all = series.flatMap((s) => s.values.filter((v): v is number => v !== null));
    const yScl = yScale(all, innerH, { baseline, yMax });
    const ticks = yScl.ticks(5);
    const labels = ticks.map((t) => yFormat(t, ticks));
    return { y: yScl, yTicks: ticks, yLabels: labels, left: yGutter(labels) };
  }, [series, baseline, yMax, innerH, yFormat]);
  const innerW = Math.max(10, width - left - right);

  const { x, xs, ticks } = useMemo(() => {
    const xScl = scaleLinear()
      .domain(xAt?.domain ?? [0, Math.max(1, n - 1)])
      .range([0, innerW]);
    return {
      x: xScl,
      xs: xAt?.values ?? Array.from({ length: n }, (_, i) => i),
      ticks: fitXTicks(xTicks, xScl, innerW),
    };
  }, [xAt, n, innerW, xTicks]);
  const xOf = (i: number) => x(xs[i]!);

  const paths = useMemo(
    () =>
      series.map((s) => {
        const pts = s.values.map((v, i) => [xs[i]!, v] as const);
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
    [series, x, xs, y, baseline],
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
    const at = x.invert(clientX - rect.left - left);
    const i = xAt ? nearestIndex(xs, at) : Math.round(at);
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
  const hx = hover !== null ? xOf(hover) : 0;
  const tipLeft = hx + left > width / 2;
  const tip = tipFormat ?? ((v: number) => yFormat(v, yTicks));

  return (
    <>
      {series.length > 1 && (
        <div className="legend">
          {series.map((s) => (
            <span key={s.id} className="legend-item">
              <span className="legend-line" style={{ background: s.color }} />
              {s.legend ?? s.label}
            </span>
          ))}
        </div>
      )}
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
              // Clip paths take the coordinates of the plot group that uses them.
              <defs>
                <clipPath id={`${clipId}-above`}>
                  <rect x={-left} y={-M.top} width={width} height={M.top + zeroY} />
                </clipPath>
                <clipPath id={`${clipId}-below`}>
                  <rect x={-left} y={zeroY} width={width} height={height} />
                </clipPath>
              </defs>
            )}
            <g transform={`translate(${left},${M.top})`}>
              {yTicks.map((t, k) => (
                <g key={t} transform={`translate(0,${y(t)})`}>
                  <line x1={0} x2={innerW} className={t === baseline ? "axis-base" : "grid"} />
                  <text x={-Y_LABEL_GAP} dy="0.32em" textAnchor="end" className="tick num">
                    {yLabels[k]}
                  </text>
                </g>
              ))}
              {zeroY !== null && !yTicks.includes(baseline!) && (
                <line x1={0} x2={innerW} y1={zeroY} y2={zeroY} className="axis-base" />
              )}
              {ticks.map((t) => (
                <g key={t.at} transform={`translate(${x(t.at)},${innerH})`}>
                  <line y1={0} y2={4} className="axis-base" />
                  <text y={16} textAnchor={xAnchor(x(t.at), innerW)} className="tick num">
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
                  <circle cx={xOf(i)} cy={py} r={4} fill={s.color} stroke="var(--bg-panel)" strokeWidth={2} />
                  {Math.abs(ly - py) > 1 && <line x1={xOf(i) + 6} y1={py} x2={innerW + 8} y2={ly} className="leader" />}
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
          <div className="tooltip" style={tipLeft ? { right: width - (hx + left) + 12 } : { left: hx + left + 12 }}>
            <div className="tooltip-x num">{xLabel(hover)}</div>
            {series.map((s) =>
              s.values[hover] !== null && s.values[hover] !== undefined ? (
                <div key={s.id} className="tooltip-row">
                  <span className="tooltip-key" style={{ background: s.color }} />
                  <span className="tooltip-value num">{tip(s.values[hover]!)}</span>
                  <span className="tooltip-label">{s.label}</span>
                </div>
              ) : null,
            )}
          </div>
        )}
      </div>
    </>
  );
}

/** Month-start ticks for a list of YYYY-MM-DD dates, thinned to fit. */
export function monthTicks(dates: string[], maxTicks = 8): XTick[] {
  const out: XTick[] = [];
  let prev = "";
  dates.forEach((d, i) => {
    const m = d.slice(0, 7);
    if (m !== prev) {
      if (prev !== "" || i === 0) out.push({ at: i, text: monthTick(d, i === 0 || m.endsWith("-01")) });
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
