import { expect, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

test("filters narrow the book and the totals follow", async ({ page }) => {
  covers("positions.side.*");
  await open(page, "/positions");
  const rows = page.locator("section[aria-label='Book'] tbody tr");
  await expect(rows).toHaveCount(22);

  await ctl(page, "positions.search").fill("bitcoin");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("IBIT");
  await ctl(page, "positions.search").fill("");

  await ctl(page, "positions.asset-class").selectOption("crypto");
  await expect(rows).toHaveCount(2);
  await ctl(page, "positions.asset-class").selectOption("all");

  await ctl(page, "positions.sleeve").selectOption("CRB Hedge");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("MESZ6");
  await ctl(page, "positions.sleeve").selectOption("all");

  await ctl(page, "positions.side.short").click();
  await expect(rows).toHaveCount(1);
  await expect(page.locator(".summary-strip .stat").filter({ hasText: "Shown" }).locator(".stat-value")).toHaveText(
    "1",
  );
  await ctl(page, "positions.side.long").click();
  await expect(rows).toHaveCount(21);
  await ctl(page, "positions.side.all").click();
  await expect(rows).toHaveCount(22);

  await ctl(page, "positions.search").fill("zzz");
  await expect(page.locator("section[aria-label='Book']")).toContainText("No positions match the filters");
});

test("every column sorts both ways", async ({ page }) => {
  covers("positions.table.sort.*");
  await open(page, "/positions");
  const sorts = page.locator('[data-control^="positions.table.sort."]');
  const n = await sorts.count();
  expect(n).toBe(14);
  const firstSymbol = () => page.locator("section[aria-label='Book'] tbody tr").first().locator(".sym").innerText();
  for (let i = 0; i < n; i++) {
    const th = sorts.nth(i).locator("xpath=ancestor::th");
    await sorts.nth(i).click();
    const first = await th.getAttribute("aria-sort");
    expect(first).toMatch(/ascending|descending/);
    await sorts.nth(i).click();
    expect(await th.getAttribute("aria-sort")).not.toBe(first);
  }
  // Symbol ascending puts BIL first.
  const symbolTh = ctl(page, "positions.table.sort.symbol").locator("xpath=ancestor::th");
  for (let k = 0; k < 3 && (await symbolTh.getAttribute("aria-sort")) !== "ascending"; k++) {
    await ctl(page, "positions.table.sort.symbol").click();
  }
  expect(await firstSymbol()).toBe("BIL");
});
