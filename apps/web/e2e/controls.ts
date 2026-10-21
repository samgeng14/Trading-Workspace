/**
 * Every interactive control in the dashboard, by its data-control id.
 *
 * Rules (enforced by controls.spec.ts):
 *   1. Every data-control rendered anywhere must match an entry here.
 *   2. Every entry here must be exercised by a spec (its exact string appears
 *      in an e2e/*.spec.ts file, either in a ctl() call or a covers() call).
 *
 * "*.x" matches any id ending in ".x"; "a.b.*" matches ids generated at
 * runtime under "a.b." (sort columns, segmented options).
 */
export const CONTROLS = [
  // shell
  "nav.overview",
  "nav.positions",
  "nav.performance",
  "nav.risk",
  "nav.trading",
  "nav.data",
  "nav.operations",
  "topbar.health",
  "topbar.kill-switch",
  "topbar.theme",
  "banner.resume",
  "killswitch.arm.reason",
  "killswitch.arm.cancel",
  "killswitch.arm.confirm",
  "killswitch.disarm.text",
  "killswitch.disarm.cancel",
  "killswitch.disarm.confirm",
  // every chart panel has a chart / table switch
  "*.view-chart",
  "*.view-table",
  // overview
  "overview.intraday.plot",
  "overview.exposure.plot",
  "overview.performance.plot",
  "overview.performance.mode.*",
  "overview.exposure.more",
  "overview.movers.more",
  "overview.performance.more",
  "overview.today.more",
  "overview.movers.sort.*",
  "overview.readiness.*",
  "overview.issue.*",
  // positions
  "positions.search",
  "positions.asset-class",
  "positions.sleeve",
  "positions.side.*",
  "positions.table.sort.*",
  // performance
  "performance.range.*",
  "performance.benchmark",
  "performance.growth.plot",
  "performance.growth.mode.*",
  "performance.drawdown.plot",
  "performance.monthly.*",
  // risk
  "risk.exposure.plot",
  "risk.scenarios.plot",
  // trading
  "trading.preview.rows.*",
  "trading.preview.submit",
  "trading.preview.table.sort.*",
  "rebalance.submit.text",
  "rebalance.submit.cancel",
  "rebalance.submit.confirm",
  "trading.orders.filter.*",
  "trading.orders.table.sort.*",
  "trading.orders.cancel",
  // operations
  "ops.kill-switch.arm",
  "ops.kill-switch.disarm",
  "ops.doctor.run",
  "ops.runs.details",
] as const;

export function matches(pattern: string, id: string): boolean {
  if (pattern.startsWith("*")) return id.endsWith(pattern.slice(1));
  if (pattern.endsWith(".*")) return id.startsWith(pattern.slice(0, -1)) && id.length > pattern.length - 1;
  return id === pattern;
}

export const isRegistered = (id: string) => CONTROLS.some((p) => matches(p, id));
