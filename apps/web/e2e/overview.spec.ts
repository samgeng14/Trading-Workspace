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
