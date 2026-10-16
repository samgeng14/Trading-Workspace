// Performance statistics on daily returns (fractions). Used by the demo
// backend; the real backend computes the same quantities in Python.

export { MIN_RATIO_OBS } from "../lib/labels";

export const DAYS_PER_YEAR = 252;

export function mean(xs: readonly number[]): number {
  if (xs.length === 0) return 0;
  let s = 0;
  for (const x of xs) s += x;
  return s / xs.length;
}

/** Sample standard deviation. */
export function stdev(xs: readonly number[]): number {
  if (xs.length < 2) return 0;
  const m = mean(xs);
  let s = 0;
  for (const x of xs) s += (x - m) ** 2;
  return Math.sqrt(s / (xs.length - 1));
}

export function compound(rets: readonly number[]): number {
  let g = 1;
  for (const r of rets) g *= 1 + r;
  return g - 1;
}

export function annualizedReturn(rets: readonly number[]): number {
  if (rets.length === 0) return 0;
  return (1 + compound(rets)) ** (DAYS_PER_YEAR / rets.length) - 1;
}

export function annualizedVol(rets: readonly number[]): number {
  return stdev(rets) * Math.sqrt(DAYS_PER_YEAR);
}

export interface SharpeEstimate {
  value: number;
  ci_low: number;
  ci_high: number;
  years: number;
}

/**
 * Annualized Sharpe of returns in excess of `rfDaily`, with a 95% interval
 * from the standard error sqrt((1 + SR^2 / 2) / T), T in years. One year of
 * data at SR = 1 gives roughly +/-2.4: short records can't confirm a Sharpe.
 */
export function sharpe(rets: readonly number[], rfDaily = 0): SharpeEstimate {
  const years = rets.length / DAYS_PER_YEAR;
  const excess = rets.map((r) => r - rfDaily);
  const sd = stdev(excess);
  const value = sd === 0 ? 0 : (mean(excess) / sd) * Math.sqrt(DAYS_PER_YEAR);
  const se = years > 0 ? Math.sqrt((1 + (value * value) / 2) / years) : Number.POSITIVE_INFINITY;
  return { value, ci_low: value - 1.96 * se, ci_high: value + 1.96 * se, years };
}

export function sortino(rets: readonly number[], rfDaily = 0): number {
  const excess = rets.map((r) => r - rfDaily);
  const downside = Math.sqrt(mean(excess.map((r) => Math.min(r, 0) ** 2)));
  return downside === 0 ? 0 : (mean(excess) / downside) * Math.sqrt(DAYS_PER_YEAR);
}

/** Drawdown from running peak for each level (<= 0). */
export function drawdowns(levels: readonly number[]): number[] {
  let peak = Number.NEGATIVE_INFINITY;
  return levels.map((v) => {
    peak = Math.max(peak, v);
    return v / peak - 1;
  });
}

export function maxDrawdown(levels: readonly number[]): number {
  return Math.min(0, ...drawdowns(levels));
}

export interface RelativeStats {
  beta: number;
  alpha_ann: number;
  tracking_error: number;
  information_ratio: number;
}

export function relative(port: readonly number[], bench: readonly number[]): RelativeStats {
  const n = Math.min(port.length, bench.length);
  const p = port.slice(0, n);
  const b = bench.slice(0, n);
  const mp = mean(p);
  const mb = mean(b);
  let cov = 0;
  let varB = 0;
  for (let i = 0; i < n; i++) {
    cov += (p[i]! - mp) * (b[i]! - mb);
    varB += (b[i]! - mb) ** 2;
  }
  const beta = varB === 0 ? 0 : cov / varB;
  const active = p.map((r, i) => r - b[i]!);
  const te = annualizedVol(active);
  return {
    beta,
    alpha_ann: (mp - beta * mb) * DAYS_PER_YEAR,
    tracking_error: te,
    information_ratio: te === 0 ? 0 : (mean(active) * DAYS_PER_YEAR) / te,
  };
}

export function hitRate(rets: readonly number[]): number {
  if (rets.length === 0) return 0;
  return rets.filter((r) => r > 0).length / rets.length;
}

/** Simple returns from a level series. */
export function returnsOf(levels: readonly number[]): number[] {
  const out: number[] = [];
  for (let i = 1; i < levels.length; i++) out.push(levels[i]! / levels[i - 1]! - 1);
  return out;
}
