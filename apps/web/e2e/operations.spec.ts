import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

test("run doctor refreshes the report", async ({ page }) => {
  await open(page, "/ops");
  const doctor = page.locator("section[aria-label='IBKR doctor']");
  await expect(doctor.locator(".panel-meta")).toContainText("ran Fri 09:00");
  await expect(doctor.locator("tbody tr")).toHaveCount(13);
  await ctl(page, "ops.doctor.run").click();
  await expect(page.locator(".toast")).toContainText("Doctor: no failures");
  await expect(doctor.locator(".panel-meta")).toContainText("ran Fri 14:1");
  await expect(doctor).toContainText("share market data");
});

test("run history expands to its steps", async ({ page }) => {
  await open(page, "/ops");
  const runs = page.locator("section[aria-label='Recent runs']");
  await expect(runs).toContainText("Failed");
  const first = runs.locator('[data-control="ops.runs.details"]').first();
  await first.click();
  await expect(first).toHaveAttribute("aria-expanded", "true");
  await expect(runs.locator(".run-step").first()).toContainText("append_daily");
  await first.click();
  await expect(runs.locator(".run-step")).toHaveCount(0);
});

test("the exit test scores each session and says why one failed", async ({ page }) => {
  await open(page, "/ops");
  const exit = page.locator("section[aria-label='Phase 0 exit test']");
  await expect(exit.locator(".panel-meta")).toContainText(/\d+ of 10 sessions in a row/);
  await expect(exit.locator("tbody tr[data-status]:not(.exit-why)")).toHaveCount(15);
  await expect(exit.locator(".exit-why").first()).toContainText("IBKR connection refused");
  await expect(exit).toContainText("bin/tradeit exit-test");
});

test("the engine shadow lists each session's comparisons with the Python runner", async ({ page }) => {
  await open(page, "/ops");
  const shadow = page.locator("section[aria-label='C++ engine, in shadow']");
  await expect(shadow.locator(".panel-meta")).toContainText(/\d+ of 10 sessions in a row matching/);
  const rows = shadow.locator("tbody tr[data-status='match']");
  await expect(rows.first()).toBeVisible();
  await expect(rows.first().locator("td").nth(1)).toContainText("Match");
  await expect(shadow.locator("tr[data-status='differs']")).toHaveCount(0);
  await expect(shadow).toContainText("bin/tradeit engine-shadow");
});
