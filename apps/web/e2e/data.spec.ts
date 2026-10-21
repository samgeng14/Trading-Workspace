import { expect, test } from "@playwright/test";
import { open } from "./helpers";

test("the Data page lists every dataset with its watermark and state", async ({ page }) => {
  await open(page, "/data");
  const panel = page.locator("section[aria-label='Datasets']");
  await expect(panel.locator(".panel-meta")).toContainText("10 of 10 current");
  await expect(panel.locator("tbody tr")).toHaveCount(10);
  await expect(panel.locator("tr[data-state='ok']").first()).toContainText("Current");
  await expect(panel.locator("tr[data-state='off']")).toContainText("Not kept here");
  await expect(panel).toContainText("bin/tradeit catchup");
});
