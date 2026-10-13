import { expect, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

const PAGES = ["/", "/performance", "/risk"];

for (const path of PAGES) {
  test(`every chart panel on ${path} switches to its table and back`, async ({ page }) => {
    covers("*.view-chart", "*.view-table");
    await open(page, path);
    const toTable = page.locator('[data-control$=".view-table"]');
    const n = await toTable.count();
    expect(n).toBeGreaterThan(1);
    for (let i = 0; i < n; i++) {
      const id = (await toTable.nth(i).getAttribute("data-control"))!;
      const panel = toTable.nth(i).locator("xpath=ancestor::section[contains(@class,'panel')]");
      await toTable.nth(i).click();
      await expect(toTable.nth(i)).toHaveAttribute("aria-pressed", "true");
      await expect(panel.locator(".figure-table tbody tr").first()).toBeVisible();
      await expect(panel.locator("svg[role=img]")).toHaveCount(0);
      await ctl(page, id.replace(".view-table", ".view-chart")).click();
      await expect(panel.locator("svg[role=img]").first()).toBeVisible();
    }
  });
}

test("overview charts: intraday keyboard crosshair, performance hover, exposure rows", async ({ page }) => {
  await open(page);
  const intraday = ctl(page, "overview.intraday.plot");
  await intraday.focus();
  await page.keyboard.press("End");
  const tip = page.locator('section[aria-label="Today\'s P&L"] .tooltip');
  await expect(tip).toContainText("Day P&L");
  await expect(tip).toContainText("ET");
  await page.keyboard.press("Home");
  // the API samples every 15 s on its own clock: the first sample lands a few seconds after the open
  await expect(tip).toContainText(/09:30:\d{2} ET/);

  const perf = ctl(page, "overview.performance.plot");
  const box = (await perf.boundingBox())!;
  await perf.hover({ position: { x: box.width * 0.4, y: box.height * 0.5 } });
  await expect(page.locator("section[aria-label='Since inception vs S&P 500'] .tooltip")).toContainText("Portfolio");

  const exposure = ctl(page, "overview.exposure.plot");
  await exposure.locator(".bar-row").first().hover();
  await expect(page.locator("section[aria-label='Exposure'] .tooltip")).toContainText("Long");
});

test("risk charts respond to hover and keyboard focus", async ({ page }) => {
  await open(page, "/risk");
  const exposure = ctl(page, "risk.exposure.plot");
  await exposure.locator(".bar-row").nth(1).hover();
  await expect(page.locator("section[aria-label='Exposure by asset class'] .tooltip")).toContainText("Net");

  const scenarios = ctl(page, "risk.scenarios.plot");
  const row = scenarios.locator(".bar-row").last();
  await expect(row).toHaveAttribute("aria-label", /March 2020 replay: −\$/);
  await row.focus();
  await expect(row).toHaveClass(/is-hover/);
});
