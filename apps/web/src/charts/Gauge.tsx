/**
 * A ring that fills a 270° sweep to `value / max`, for a share of a limit (gross exposure
 * against 100% of NAV). Decorative: the figure it shows is printed beside it, so it is hidden
 * from assistive tech.
 */
export function Gauge({
  value,
  max = 1,
  size = 50,
  stroke = 5,
  tone = "accent",
  text,
}: {
  value: number;
  max?: number;
  size?: number;
  stroke?: number;
  tone?: "accent" | "warn" | "bad";
  /** Short text in the middle, e.g. "94%". */
  text?: string;
}) {
  const r = (size - stroke) / 2;
  const c = 2 * Math.PI * r;
  const sweep = c * 0.75;
  const share = Math.min(1, Math.max(0, max > 0 ? value / max : 0));
  const color = tone === "bad" ? "var(--status-critical)" : tone === "warn" ? "var(--status-warning)" : "var(--accent)";
  const id = `gauge-${tone}`;
  return (
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} className="gauge" aria-hidden="true">
      <defs>
        <linearGradient id={id} x1="0" y1="1" x2="1" y2="0">
          <stop offset="0" stopColor="var(--accent-2)" />
          <stop offset="1" stopColor={color} />
        </linearGradient>
      </defs>
      <g transform={`rotate(135 ${size / 2} ${size / 2})`}>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={`${sweep} ${c}`}
          className="gauge-track"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={r}
          fill="none"
          stroke={tone === "accent" ? `url(#${id})` : color}
          strokeWidth={stroke}
          strokeLinecap="round"
          // a gap of two circumferences, so the offset never wraps a second dash into view
          strokeDasharray={`${sweep} ${2 * c}`}
          strokeDashoffset={sweep * (1 - share)}
          className="gauge-arc"
          style={{ ["--gauge-len" as string]: `${sweep}px`, filter: `drop-shadow(0 0 4px ${color})` }}
        />
      </g>
      {text && (
        <text x={size / 2} y={size / 2 + 4} textAnchor="middle" className="gauge-text">
          {text}
        </text>
      )}
    </svg>
  );
}
