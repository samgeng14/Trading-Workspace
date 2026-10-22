import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

test("the Strategies page lists every sleeve and charts the shadow book beside the live fund", async ({ page }) => {
  await open(page, "/strategies");
  const sleeves = page.locator("section[aria-label='Sleeves']");
  await expect(sleeves.locator("tbody tr")).toHaveCount(8);
  await expect(sleeves.locator("tr[data-status='shadow']")).toHaveCount(2);
  await expect(sleeves.locator("tr[data-status='shadow']").first()).toContainText("S0 with a trend overlay");
  await expect(sleeves.locator("tr[data-status='failed']").first()).toContainText("Failed the gate");
  const shadow = page.locator("section[aria-label='Shadow book']");
  await expect(shadow.locator(".panel-meta")).toContainText(/since .* sessions/);
  const plot = ctl(page, "strategies.shadow.plot");
  await expect(plot).toBeVisible();
  await plot.hover();
  await expect(shadow).toContainText("Live S0");
  await expect(page.locator("section[aria-label='What the shadow book would do']")).toContainText("Buy BIL");
});
