import { expect, test } from "@playwright/test";
import { AFTER_REBALANCE, ctl, open } from "./helpers";

test("the Command Center answers first, and its link opens Trading", async ({ page }) => {
  await open(page);
  const answer = page.locator("section[aria-label='Trading today']");
  await expect(answer.getByTestId("trading-day")).toContainText("Not traded yet today");
  await expect(answer).toContainText("The automatic run is at 15:40 ET: market-on-close");
  // the Command Center has no button: rebalancing starts from Trading
  await expect(answer.locator("[data-control='trading.today.rebalance']")).toHaveCount(0);
  await ctl(page, "overview.trading-day.more").click();
  await expect(page).toHaveURL(/\/trading$/);
});

test("after the day's rebalance filled, Trading says so and shows no basket", async ({ page }) => {
  await open(page, "/trading", AFTER_REBALANCE);
  const today = page.getByTestId("trading-day");
  await expect(today).toHaveAttribute("data-state", "done");
  await expect(today).toContainText("Traded at today's close (orders sent 15:40 ET)");
  await expect(today).toContainText(/Filled (\d+)\/\1 at the 16:00 ET close\./);
  await expect(ctl(page, "trading.today.rebalance")).toHaveCount(0);
  await expect(page.locator("section[aria-label='Rebalance preview']")).toHaveCount(0);
  await expect(page.locator(".checks")).toHaveCount(0); // nothing red about trades already done
  await expect(page.locator("section[aria-label='Orders'] tbody tr").first()).toContainText("Filled");
});

test("on a closed day it names the next run", async ({ page }) => {
  await open(page, "/trading", "2026-10-10T16:00:00Z");
  const today = page.getByTestId("trading-day");
  await expect(today).toContainText("No trading today: the market is closed");
  await expect(today).toContainText("The next automatic run is Mon 15:40 ET.");
  await expect(ctl(page, "trading.today.rebalance")).toHaveCount(0);
});
