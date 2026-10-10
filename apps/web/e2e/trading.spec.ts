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
  await expect(rows).toHaveCount(22);
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

test("submitting needs SUBMIT typed; orders appear working, can be cancelled, and the duplicate guard holds", async ({
  page,
}) => {
  covers("trading.orders.filter.*");
  await open(page, "/trading");
  const submit = ctl(page, "trading.preview.submit");
  await expect(submit).toBeEnabled();
  await submit.click();
  await expect(ctl(page, "rebalance.submit.confirm")).toBeDisabled();
  await ctl(page, "rebalance.submit.text").fill("submit");
  await expect(ctl(page, "rebalance.submit.confirm")).toBeDisabled();
  await ctl(page, "rebalance.submit.cancel").click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await submit.click();
  await ctl(page, "rebalance.submit.text").fill("SUBMIT");
  await ctl(page, "rebalance.submit.confirm").click();
  await expect(page.locator(".toast")).toContainText("MOC orders queued");

  const blotter = page.locator("section[aria-label='Orders']");
  await ctl(page, "trading.orders.filter.working").click();
  const working = blotter.locator("tbody tr");
  await expect(working.first()).toContainText("Working");
  const count = await working.count();
  expect(count).toBeGreaterThan(5);

  await expect(submit).toBeDisabled();
  await expect(page.locator(".preview-submit")).toContainText("Blocked: duplicate guard");

  await working.first().locator('[data-control="trading.orders.cancel"]').click();
  await expect(working).toHaveCount(count - 1);
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
