import { expect, test } from "@playwright/test";
import { AFTER_REBALANCE, covers, ctl, open } from "./helpers";

test("rebalance preview: rows toggle and sortable columns", async ({ page }) => {
  covers("trading.preview.rows.*", "trading.preview.table.sort.*");
  await open(page, "/trading");
  const preview = page.locator("section[aria-label='Rebalance preview']");
  const rows = preview.locator("tbody tr");
  const orders = Number(await preview.locator(".preview-totals .stat").first().locator(".stat-value").innerText());
  await expect(rows).toHaveCount(orders);
  await ctl(page, "trading.preview.rows.all").click();
  await expect(rows).toHaveCount(21); // the 21-ETF model; TQQQ left it
  await ctl(page, "trading.preview.rows.trades").click();
  await expect(rows).toHaveCount(orders);

  const sorts = preview.locator('[data-control^="trading.preview.table.sort."]');
  const n = await sorts.count();
  expect(n).toBe(8);
  for (let i = 0; i < n; i++) {
    const header = sorts.nth(i);
    await header.click();
    await expect(header.locator("xpath=ancestor::th")).toHaveAttribute("aria-sort", /ascending|descending/);
  }
});

test("rebalancing needs SUBMIT typed; its steps show live, then its orders work, cancel, and nothing sends twice", async ({
  page,
}) => {
  covers("trading.orders.filter.*");
  await open(page, "/trading");
  // the normal rebalance passes the engine's no-leverage check on the whole book: that is why it can go
  const leverage = page.locator("ul.checks li").filter({ hasText: "No leverage" });
  await expect(leverage).toContainText(/gross \d+\.\d% → \d+\.\d% of NAV, cash \$[\d,]+ after; every holding counted/);
  await expect(leverage.locator('circle[fill="var(--status-good)"]')).toHaveCount(1);
  const today = page.getByTestId("trading-day");
  await expect(today).toContainText("Not traded yet today");
  const rebalance = ctl(page, "trading.today.rebalance");
  await expect(rebalance).toBeEnabled();
  await expect(page.locator(".today-why")).toHaveText(/^About \d+ orders\. Requires typed confirmation\.$/);
  await rebalance.click();
  await expect(ctl(page, "rebalance.submit.confirm")).toBeDisabled();
  await ctl(page, "rebalance.submit.text").fill("submit");
  await expect(ctl(page, "rebalance.submit.confirm")).toBeDisabled();
  await ctl(page, "rebalance.submit.cancel").click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await rebalance.click();
  await ctl(page, "rebalance.submit.text").fill("SUBMIT");
  await ctl(page, "rebalance.submit.confirm").click();
  await expect(page.locator(".toast")).toContainText("MOC orders queued");

  // the run's own steps, one by one
  const steps = page.getByRole("list", { name: "Rebalance steps" });
  await expect(today).toContainText("Rebalancing now:");
  await expect(steps.locator("li")).toHaveCount(6);
  await expect(steps.locator('li[data-state="running"]')).toHaveCount(1);
  // then the orders are in, waiting for the close, and the basket is gone: nothing to send twice
  await expect(today).toContainText(/Orders in for today's close \(sent 14:1\d ET\)/, { timeout: 10_000 });
  await expect(today).toContainText(/Filled 0\/\d+; \d+ working, they fill at the 16:00 ET close\./);
  await expect(steps.locator("li")).toHaveCount(7);
  await expect(steps.locator("li").last()).toContainText(/Fills.*filled 0\/\d+/);
  await expect(steps.locator('li[data-state="done"]')).toHaveCount(6);
  await expect(rebalance).toHaveCount(0);
  await expect(page.locator("section[aria-label='Rebalance preview']")).toHaveCount(0);

  const blotter = page.locator("section[aria-label='Orders']");
  await ctl(page, "trading.orders.filter.working").click();
  const working = blotter.locator("tbody tr");
  await expect(working.first()).toContainText("Working");
  const count = await working.count();
  expect(count).toBeGreaterThan(5);

  await working.first().locator('[data-control="trading.orders.cancel"]').click();
  await expect(working).toHaveCount(count - 1);
  await expect(today).toContainText("1 cancelled or rejected");
  await ctl(page, "trading.orders.filter.cancelled").click();
  await expect(blotter.locator("tbody tr")).toHaveCount(1);
  await expect(blotter.locator("tbody tr").first()).toContainText("Cancelled");
  await ctl(page, "trading.orders.filter.all").click();
  await expect(blotter.locator("tbody tr")).toHaveCount(count);
});

test("blotter after a rebalance: fills, slippage and sorting", async ({ page }) => {
  covers("trading.orders.table.sort.*");
  await open(page, "/trading", AFTER_REBALANCE);
  const blotter = page.locator("section[aria-label='Orders']");
  await ctl(page, "trading.orders.filter.filled").click();
  const rows = blotter.locator("tbody tr");
  await expect(rows.first()).toContainText("Filled");
  expect(await rows.count()).toBeGreaterThan(10);
  await expect(blotter.locator(".panel-foot")).toContainText("bp");
  await expect(blotter.locator('[data-control="trading.orders.cancel"]')).toHaveCount(0);

  const sorts = blotter.locator('[data-control^="trading.orders.table.sort."]');
  const n = await sorts.count();
  expect(n).toBe(12);
  for (let i = 0; i < n; i++) {
    await sorts.nth(i).click();
    await expect(sorts.nth(i).locator("xpath=ancestor::th")).toHaveAttribute("aria-sort", /ascending|descending/);
  }
});
