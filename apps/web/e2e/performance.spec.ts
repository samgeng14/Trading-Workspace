import { expect, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

test("period presets change the window", async ({ page }) => {
  covers("performance.range.*");
  await open(page, "/performance");
  const sub = page.locator(".page-sub");
  await expect(sub).toContainText("Oct 15, 2025");
  const expected: Record<string, RegExp> = {
    "1M": /Sep \d+, 2026/,
    "3M": /Jul \d+, 2026/,
    "6M": /Apr \d+, 2026/,
    YTD: /Dec 31, 2025/,
    "1Y": /Oct 15, 2025/,
    ITD: /Oct 15, 2025/,
  };
  for (const [range, start] of Object.entries(expected)) {
    await ctl(page, `performance.range.${range}`).click();
    await expect(ctl(page, `performance.range.${range}`)).toHaveAttribute("aria-pressed", "true");
    await expect(sub).toContainText(start);
  }
});

test("benchmark switch relabels everything", async ({ page }) => {
  await open(page, "/performance");
  await ctl(page, "performance.benchmark").selectOption("QQQ");
  await expect(page.locator("section[aria-label='Growth of $100 vs Nasdaq-100']")).toBeVisible();
  await expect(page.locator(".summary-strip")).toContainText("Beta to Nasdaq-100");
  await ctl(page, "performance.benchmark").selectOption("6040");
  await expect(page.locator("section[aria-label='Growth of $100 vs 60/40']")).toBeVisible();
});

test("monthly grid switches between portfolio, benchmark and excess", async ({ page }) => {
  covers("performance.monthly.*");
  await open(page, "/performance");
  const panel = page.locator("section[aria-label='Monthly returns']");
  await expect(panel.locator(".panel-meta")).toContainText("portfolio");
  const firstCell = () => panel.locator("tbody tr").last().locator("td").nth(1).innerText();
  const port = await firstCell();
  await ctl(page, "performance.monthly.benchmark").click();
  await expect(panel.locator(".panel-meta")).toContainText("S&P 500");
  expect(await firstCell()).not.toBe(port);
  await ctl(page, "performance.monthly.excess").click();
  await expect(panel.locator(".panel-meta")).toContainText("portfolio minus S&P 500");
  await ctl(page, "performance.monthly.portfolio").click();
  expect(await firstCell()).toBe(port);
});

test("growth and drawdown charts answer the pointer and the keyboard", async ({ page }) => {
  await open(page, "/performance");
  const growth = ctl(page, "performance.growth.plot");
  const box = (await growth.boundingBox())!;
  await growth.hover({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
  const tip = page.locator("section[aria-label^='Growth of $100'] .tooltip");
  await expect(tip).toContainText("Portfolio");
  await expect(tip).toContainText("S&P 500");

  const dd = ctl(page, "performance.drawdown.plot");
  await dd.focus();
  await page.keyboard.press("End");
  await expect(page.locator("section[aria-label='Drawdown'] .tooltip")).toContainText("Drawdown");
  await page.keyboard.press("Escape");
  await expect(page.locator("section[aria-label='Drawdown'] .tooltip")).toHaveCount(0);
});
