import { expect, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

test("the Strategies page lists every sleeve and charts each shadow book beside the live fund", async ({ page }) => {
  covers("strategies.shadow.*");
  await open(page, "/strategies");
  const sleeves = page.locator("section[aria-label='Sleeves']");
  await expect(sleeves.locator("tbody tr")).toHaveCount(9);
  await expect(sleeves.locator("tr[data-status='shadow']")).toHaveCount(2);
  await expect(sleeves.locator("tr[data-status='shadow']").first()).toContainText("S0 with a trend overlay");
  await expect(sleeves.locator("tr[data-status='failed']").first()).toContainText("Failed the gate");

  const h2 = page.locator("section[aria-label='Shadow book H2']");
  await expect(h2.locator(".panel-meta").first()).toContainText(/since .* sessions/);
  const plot = ctl(page, "strategies.shadow.h2.plot");
  await expect(plot).toBeVisible();
  await plot.hover();
  await expect(h2).toContainText("Live S0");
  await expect(h2.locator("section[aria-label='What H2 would do']")).toContainText("Buy BIL");

  const r3 = page.locator("section[aria-label='Shadow book R3']");
  await expect(ctl(page, "strategies.shadow.r3.plot")).toBeVisible();
  const would = r3.locator("section[aria-label='What R3 would do']");
  await expect(would).toContainText("contango: short the next session");
  await expect(would).toContainText("VXM 2026-11-18");
});
