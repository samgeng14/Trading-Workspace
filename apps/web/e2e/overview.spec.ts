import { expect, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

test("headline numbers and panels render", async ({ page }) => {
  await open(page);
  await expect(page.getByTestId("nav")).toHaveText(/^\$1,\d{3},\d{3}$/);
  await expect(page.getByTestId("kpi-day")).toContainText("$");
  await expect(page.locator("section[aria-label='Risk limits'] .meter-row")).toHaveCount(8);
  await expect(page.locator("section[aria-label='Schedule'] .schedule-row")).toHaveCount(7);
  await expect(page.locator(".next-job")).toContainText("Finalize orders");
});

test("panel links go to the detail pages", async ({ page }) => {
  const links = [
    ["overview.exposure.more", "/risk"],
    ["overview.movers.more", "/positions"],
    ["overview.performance.more", "/performance"],
    ["overview.today.more", "/trading"],
  ] as const;
  for (const [id, path] of links) {
    await open(page);
    await ctl(page, id).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
  }
});

test("movers table sorts by any column", async ({ page }) => {
  covers("overview.movers.sort.*");
  await open(page);
  const sorts = page.locator('[data-control^="overview.movers.sort."]');
  expect(await sorts.count()).toBe(6);
  for (let i = 0; i < 6; i++) {
    await sorts.nth(i).click();
    await expect(sorts.nth(i).locator("xpath=ancestor::th")).toHaveAttribute("aria-sort", /ascending|descending/);
  }
});

test("readiness lists every check and each row opens the page that owns it", async ({ page }) => {
  covers("overview.readiness.*");
  await open(page);
  const rows = page.locator('[data-control^="overview.readiness."]');
  await expect(rows).toHaveCount(7);
  await expect(ctl(page, "overview.readiness.gateway")).toContainText("Connected");
  await expect(ctl(page, "overview.readiness.market_data")).toContainText("Delayed 15 min");
  const targets = [
    ["overview.readiness.pretrade", "/trading"],
    ["overview.readiness.limits", "/risk"],
    ["overview.readiness.doctor", "/ops"],
  ] as const;
  for (const [id, path] of targets) {
    await open(page);
    await ctl(page, id).click();
    await expect(page).toHaveURL(new RegExp(`${path}$`));
  }
});

test("verdict, blockers and warnings follow the kill switch", async ({ page }) => {
  covers("overview.issue.*");
  await open(page);
  // demo book at this moment: forecast vol is over its limit (blocker), quotes are delayed (warning)
  await expect(page.getByTestId("verdict")).toContainText("Action required");
  await expect(ctl(page, "overview.issue.limit.vol")).toContainText("vs limit");
  await expect(ctl(page, "overview.issue.market_data")).toContainText("Delayed 15 min");
  await expect(ctl(page, "overview.issue.kill_switch")).toHaveCount(0);

  await ctl(page, "topbar.kill-switch").click();
  await ctl(page, "killswitch.arm.reason").fill("verdict check");
  await ctl(page, "killswitch.arm.confirm").click();
  await expect(ctl(page, "overview.issue.kill_switch")).toContainText("verdict check");
  await expect(ctl(page, "overview.readiness.kill_switch")).toContainText("Halted");
  await expect(page.locator(".topbar")).toContainText("Halted");

  await ctl(page, "overview.issue.kill_switch").click();
  await expect(page).toHaveURL(/\/ops$/);
});
