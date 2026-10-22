// The fund's no-leverage mandate for the demo: a port of the C++ engine's
// pre-trade check (engine/src/risk.cpp), with the real API's policy
// (services/api/dv02_api/mandate.py POLICY).
//
//   gross = (long + |short|) / NAV <= 100%, futures at notional and
//           leveraged or inverse ETFs at their economic exposure
//   cash >= 0 after the orders: no margin loan
//
// check() takes the whole book plus the orders about to go and says whether
// the book after them stays inside the policy. A breach blocks unless the
// orders only shrink positions and leave that measure no worse. Messages are
// the engine's, word for word, so the demo preview reads like the real one.

export interface Policy {
  maxGross: number;
  minCash: number;
  maxNet: number;
  minNet: number;
  maxSingleName: number;
}

/** dv02_engine.risk.Policy as the API runs it. */
export const POLICY: Policy = { maxGross: 1, minCash: 0, maxNet: 1, minNet: -1, maxSingleName: 0.25 };

export interface BookPosition {
  symbol: string;
  quantity: number; // signed: negative is short
  price: number;
  multiplier: number;
  /** Futures move no cash when traded (only margin); everything else pays quantity x price x multiplier. */
  settlesInCash: boolean;
  /** Economic exposure per dollar of market value: 3 for a 3x ETF, -1 for an inverse one. */
  exposure: number;
}

export interface BookOrder extends BookPosition {
  commission: number;
}

export interface Book {
  nav: number;
  cash: number;
  positions: BookPosition[];
}

export interface Exposure {
  nav: number;
  cash: number;
  longValue: number;
  shortValue: number; // positive
  gross: number;
  net: number;
  cashRatio: number;
  largestName: string;
  largestWeight: number;
}

export interface Breach {
  rule: "nav" | "cash" | "unpriced" | "gross" | "net" | "single_name";
  blocking: boolean;
  value: number;
  limit: number;
  message: string;
}

export interface Result {
  ok: boolean;
  reduceOnly: boolean;
  before: Exposure;
  after: Exposure;
  breaches: Breach[];
}

const TOL = 1e-9;

/** The engine's money(): "$1,234" / "-$1,234", whole dollars ("%.0f" digits, so "$nan" too). */
export function money(v: number): string {
  const r = Math.round(Math.abs(v)); // std::round(std::fabs(v)): halves away from zero
  const d = Number.isNaN(r) ? "nan" : r === Infinity ? "inf" : BigInt(r).toString();
  let out = "";
  for (let i = 0; i < d.length; i++) {
    if (i > 0 && (d.length - i) % 3 === 0) out += ",";
    out += d[i];
  }
  return `${v < 0 && r > 0 ? "-$" : "$"}${out}`;
}

/**
 * The engine's pct(): "%.1f" of v * 100, "99.5%". printf rounds an exact tie
 * to even (33.25 -> "33.2", as Python's "{:.1%}" does) where toFixed rounds it
 * away from zero, so ties are settled here.
 */
export function pct(v: number): string {
  const d = v * 100;
  let s = Number.isNaN(d) ? "nan" : !Number.isFinite(d) ? (d < 0 ? "-inf" : "inf") : d.toFixed(1);
  // d is a tie exactly when 4d is an odd integer (x.25 or x.75); 4d and 10|d| are exact here
  if (Number.isInteger(d * 4) && (d * 4) % 2 !== 0 && Math.abs(d) < 1e14) {
    const tenths = Math.floor(Math.abs(d) * 10);
    s = `${d < 0 ? "-" : ""}${((tenths % 2 === 0 ? tenths : tenths + 1) / 10).toFixed(1)}`;
  }
  if (Object.is(d, -0)) s = "-0.0";
  return `${s}%`;
}

/** printf's "%g" (six significant digits), for the engine's " %gx" on a geared holding: 3 -> "3", 1/3 -> "0.333333". */
function gearing(x: number): string {
  if (x === 0) return Object.is(x, -0) ? "-0" : "0";
  const trim = (s: string) => (s.includes(".") ? s.replace(/0+$/, "").replace(/\.$/, "") : s);
  const [mantissa, e] = x.toExponential(5).split("e") as [string, string];
  const exp = Number(e);
  if (exp < -4 || exp >= 6) return `${trim(mantissa)}e${exp < 0 ? "-" : "+"}${String(Math.abs(exp)).padStart(2, "0")}`;
  return trim(x.toFixed(5 - exp));
}

const notional = (p: BookPosition) => p.quantity * p.price * p.multiplier * p.exposure;

function priced(p: BookPosition): boolean {
  if (p.quantity === 0) return true;
  return (
    Number.isFinite(p.quantity) &&
    Number.isFinite(p.price) &&
    p.price > 0 &&
    Number.isFinite(p.multiplier) &&
    p.multiplier > 0 &&
    Number.isFinite(p.exposure) &&
    p.exposure !== 0
  );
}

function join(items: string[], max = 8): string {
  const out = items.slice(0, max).join(", ");
  return items.length > max ? `${out} and ${items.length - max} more` : out;
}

export function exposure(book: Book): Exposure {
  const e: Exposure = {
    nav: book.nav,
    cash: book.cash,
    longValue: 0,
    shortValue: 0,
    gross: 0,
    net: 0,
    cashRatio: 0,
    largestName: "",
    largestWeight: 0,
  };
  const byName = new Map<string, number>();
  for (const p of book.positions) {
    if (p.quantity === 0 || !priced(p)) continue;
    const n = notional(p);
    if (n > 0) e.longValue += n;
    else e.shortValue -= n;
    byName.set(p.symbol, (byName.get(p.symbol) ?? 0) + n);
  }
  if (book.nav > 0) {
    e.gross = (e.longValue + e.shortValue) / book.nav;
    e.net = (e.longValue - e.shortValue) / book.nav;
    e.cashRatio = book.cash / book.nav;
    for (const [name, n] of [...byName].sort(([a], [b]) => (a < b ? -1 : 1))) {
      const w = Math.abs(n) / book.nav;
      if (w > e.largestWeight) {
        e.largestWeight = w;
        e.largestName = name;
      }
    }
  }
  return e;
}

/** The book after the orders fill at their prices; commissions come out of cash and NAV. */
export function apply(book: Book, orders: BookOrder[]): Book {
  const out: Book = { nav: book.nav, cash: book.cash, positions: book.positions.map((p) => ({ ...p })) };
  for (const o of orders) {
    if (o.quantity === 0) continue;
    const held = out.positions.find((p) => p.symbol === o.symbol);
    if (!held) {
      const { symbol, quantity, price, multiplier, settlesInCash, exposure } = o;
      out.positions.push({ symbol, quantity, price, multiplier, settlesInCash, exposure });
    } else {
      // re-mark what is already held at the expected fill price
      if (held.price > 0) out.nav += held.quantity * (o.price - held.price) * held.multiplier;
      held.quantity += o.quantity;
      held.price = o.price;
    }
    if (o.settlesInCash) out.cash -= o.quantity * o.price * o.multiplier;
    out.cash -= o.commission;
    out.nav -= o.commission;
  }
  return out;
}

export function check(book: Book, orders: BookOrder[], policy: Policy = POLICY): Result {
  const breaches: Breach[] = [];
  let ok = true;

  // inputs: anything unmeasurable blocks
  if (!Number.isFinite(book.nav) || book.nav <= 0) {
    ok = false;
    breaches.push({
      rule: "nav",
      blocking: true,
      value: book.nav,
      limit: 0,
      message: `NAV is ${money(book.nav)}, so leverage can't be measured`,
    });
  }
  if (!Number.isFinite(book.cash)) {
    ok = false;
    breaches.push({
      rule: "cash",
      blocking: true,
      value: 0,
      limit: policy.minCash,
      message: "cash balance is missing",
    });
  }
  const unpriced: string[] = [];
  const geared: string[] = [];
  const noteGearing = (symbol: string, x: number) => {
    if (x === 1 || !Number.isFinite(x)) return;
    const item = `${symbol} ${gearing(x)}x`;
    if (!geared.includes(item)) geared.push(item);
  };
  for (const p of book.positions) {
    if (!priced(p)) unpriced.push(p.symbol);
    if (p.quantity !== 0) noteGearing(p.symbol, p.exposure);
  }
  const traded = new Map<string, number>();
  for (const o of orders) {
    if (!priced(o) || !Number.isFinite(o.commission)) unpriced.push(o.symbol);
    if (o.quantity !== 0) noteGearing(o.symbol, o.exposure);
    traded.set(o.symbol, (traded.get(o.symbol) ?? 0) + o.quantity);
  }
  if (unpriced.length) {
    ok = false;
    breaches.push({
      rule: "unpriced",
      blocking: true,
      value: unpriced.length,
      limit: 0,
      message: `no usable price for ${join(unpriced)}, so leverage can't be measured`,
    });
  }

  // reduce-only: every traded name moves toward zero without flipping
  let reduceOnly = true;
  for (const [symbol, dq] of [...traded].sort(([a], [b]) => (a < b ? -1 : 1))) {
    if (dq === 0) continue;
    const q0 = book.positions.filter((p) => p.symbol === symbol).reduce((a, p) => a + p.quantity, 0);
    const q1 = q0 + dq;
    if (Math.abs(q1) > Math.abs(q0) + TOL || q0 * q1 < 0) {
      reduceOnly = false;
      break;
    }
  }

  const before = exposure(book);
  const after = exposure(apply(book, orders));
  const result = (): Result => ({ ok, reduceOnly, before, after, breaches });
  if (!(after.nav > 0)) return result();

  const judge = (
    rule: Breach["rule"],
    breached: boolean,
    worse: boolean,
    value: number,
    limit: number,
    what: string,
  ) => {
    if (!breached) return;
    const blocking = !(reduceOnly && !worse);
    breaches.push({
      rule,
      blocking,
      value,
      limit,
      message: blocking ? what : `${what}; these orders only reduce positions, so they may go`,
    });
    if (blocking) ok = false;
  };
  const a = after;
  const b = before;
  const grossDollars = a.longValue + a.shortValue;
  judge(
    "gross",
    a.gross > policy.maxGross + TOL,
    a.gross > b.gross + TOL,
    a.gross,
    policy.maxGross,
    `gross ${pct(a.gross)} of NAV after these orders, over the ${pct(policy.maxGross)} limit by ${money(
      grossDollars - policy.maxGross * a.nav,
    )}${geared.length ? ` (counting ${join(geared)} at that exposure)` : ""}`,
  );
  judge(
    "cash",
    a.cashRatio < policy.minCash - TOL,
    a.cashRatio < b.cashRatio - TOL,
    a.cashRatio,
    policy.minCash,
    `cash ${money(a.cash)} after these orders${
      policy.minCash === 0 ? ": a margin loan" : `, under the ${pct(policy.minCash)} floor`
    }, short by ${money(policy.minCash * a.nav - a.cash)}`,
  );
  // |net| <= gross, so a net limit at or beyond the gross limit can only repeat it
  if (policy.maxNet < policy.maxGross)
    judge(
      "net",
      a.net > policy.maxNet + TOL,
      a.net > b.net + TOL,
      a.net,
      policy.maxNet,
      `net ${pct(a.net)} of NAV after these orders, over the ${pct(policy.maxNet)} limit`,
    );
  if (policy.minNet > -policy.maxGross)
    judge(
      "net",
      a.net < policy.minNet - TOL,
      a.net < b.net - TOL,
      a.net,
      policy.minNet,
      `net ${pct(a.net)} of NAV after these orders, under the ${pct(policy.minNet)} limit`,
    );
  judge(
    "single_name",
    a.largestWeight > policy.maxSingleName + TOL,
    a.largestWeight > b.largestWeight + TOL,
    a.largestWeight,
    policy.maxSingleName,
    `${a.largestName} is ${pct(a.largestWeight)} of NAV after these orders, over the ${pct(policy.maxSingleName)} limit`,
  );
  return result();
}
