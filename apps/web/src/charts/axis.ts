// Axis and layout arithmetic for LineChart, kept apart from React so it can be tested.

import { bisector, extent } from "d3-array";
import { scaleLinear } from "d3-scale";
import { MINUS, money } from "../lib/format";

/** A tick on the x axis, at an x position in the chart's own units (point index, minutes into a session…). */
export interface XTick {
  at: number;
  text: string;
}

// Tick text is IBM Plex Mono at --fs-2xs (10.5px), where every glyph is 0.6em (6.3px).
// A little more covers the monospace fallbacks.
const TICK_CHAR_W = 6.5;
/** Clear space between a y label and the plot: more than a hover dot's radius plus its ring (4 + 2). */
export const Y_LABEL_GAP = 8;
const X_LABEL_GAP = 10;
/** Within this distance of an end, x labels sit flush with it instead of centred on their tick. */
const X_EDGE = 24;

export const tickTextWidth = (text: string) => Math.ceil(text.length * TICK_CHAR_W);

/** Width of the left gutter that holds the y tick labels. The plot starts after it, so no label can touch a point. */
export function yGutter(labels: string[]): number {
  return Math.max(0, ...labels.map(tickTextWidth)) + Y_LABEL_GAP;
}

/** y scale over the values (and baseline), padded 8%, rounded to nice ticks, capped at yMax. */
export function yScale(values: number[], innerH: number, opts: { baseline?: number; yMax?: number } = {}) {
  const { baseline, yMax } = opts;
  const all = baseline !== undefined ? [...values, baseline] : values;
  const [lo = 0, hi = 1] = extent(all);
  // A flat series (or float noise around one value) still gets a readable axis.
  const span = hi - lo > 1e-9 ? hi - lo : Math.abs(hi) > 1e-9 ? Math.abs(hi) : 1;
  const pad = span * 0.08;
  const top = yMax !== undefined ? Math.min(hi + pad, yMax) : hi + pad;
  const y = scaleLinear()
    .domain([lo - pad, top])
    .nice(5)
    .range([innerH, 0]);
  if (yMax !== undefined) y.domain([y.domain()[0]!, Math.min(y.domain()[1]!, yMax)]);
  return y;
}

export function xAnchor(px: number, innerW: number): "start" | "middle" | "end" {
  return px < X_EDGE ? "start" : px > innerW - X_EDGE ? "end" : "middle";
}

/** Every k-th tick, for the smallest k whose labels clear each other at this width. */
export function fitXTicks(ticks: XTick[], xOf: (at: number) => number, innerW: number): XTick[] {
  for (let k = 1; k <= ticks.length; k++) {
    const kept = ticks.filter((_, i) => i % k === 0);
    let right = -Infinity;
    const clear = kept.every((t) => {
      const px = xOf(t.at);
      const w = tickTextWidth(t.text);
      const anchor = xAnchor(px, innerW);
      const left = anchor === "start" ? px : anchor === "end" ? px - w : px - w / 2;
      const ok = left >= right + X_LABEL_GAP;
      right = left + w;
      return ok;
    });
    if (clear) return kept;
  }
  return [];
}

/** Index of the x nearest to `x`; `xs` ascending. */
export function nearestIndex(xs: readonly number[], x: number): number {
  return xs.length === 0 ? -1 : bisector((d: number) => d).center(xs, x);
}

/**
 * Each point's place in the session in minutes after the open, from its own timestamp, so any
 * sampling interval (or a late start) lands where it happened. Points outside the session are dropped.
 */
export function sessionMinutes<T extends { t: string }>(
  points: readonly T[],
  open: string,
  close: string,
): (T & { minute: number })[] {
  const o = Date.parse(open);
  const length = (Date.parse(close) - o) / 60_000;
  return points
    .map((p) => ({ ...p, minute: (Date.parse(p.t) - o) / 60_000 }))
    .filter((p) => p.minute >= 0 && p.minute <= length)
    .sort((a, b) => a.minute - b.minute);
}

/** Decimals that tell neighbouring ticks apart, counted in `unit`s (d3 steps are 1, 2 or 5 × 10^k). */
export function tickDecimals(ticks: readonly number[], unit = 1): number {
  const step = ticks.length > 1 ? Math.abs(ticks[1]! - ticks[0]!) / unit : 1;
  return Math.max(0, Math.ceil(-Math.log10(step) - 1e-9));
}

/**
 * Dollar tick labels in one unit, with just enough decimals to tell neighbours apart: $0.95M, $1.00M, $1.05M.
 * Ticks too close for that (under $1,000 apart in millions) are whole dollars: $1,000,200.
 */
export function dollarTicks(ticks: readonly number[]): (v: number) => string {
  const big = Math.max(0, ...ticks.map(Math.abs));
  const [unit, suffix] = big >= 1e6 ? [1e6, "M"] : big >= 1e3 ? [1e3, "K"] : [1, ""];
  const decimals = tickDecimals(ticks, unit);
  if (decimals > 3) return (v) => money(v, { decimals: tickDecimals(ticks) });
  return (v) => {
    const text = (Math.abs(v) / unit).toFixed(decimals);
    if (Number(text) === 0) return "$0";
    return `${v < 0 ? MINUS : ""}$${text}${suffix}`;
  };
}
