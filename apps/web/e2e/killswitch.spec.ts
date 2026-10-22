import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

test("arming from the top bar needs a reason, halts trading, and disarming needs RESUME", async ({ page }) => {
  await open(page, "/trading");
  await ctl(page, "topbar.kill-switch").click();
  await expect(page.getByRole("dialog")).toContainText("Halt all trading?");
  await expect(ctl(page, "killswitch.arm.confirm")).toBeDisabled();
  await ctl(page, "killswitch.arm.cancel").click();
  await expect(page.getByRole("dialog")).toHaveCount(0);

  await ctl(page, "topbar.kill-switch").click();
  await ctl(page, "killswitch.arm.reason").fill("reconcile mismatch on TLT");
  await ctl(page, "killswitch.arm.confirm").click();
  const banner = page.locator(".kill-banner");
  await expect(banner).toContainText("Trading halted");
  await expect(banner).toContainText("reconcile mismatch on TLT");
  await expect(ctl(page, "topbar.kill-switch")).toHaveCount(0);
  await expect(page.locator(".checks")).toContainText("Armed: reconcile mismatch on TLT");
  await expect(ctl(page, "trading.preview.submit")).toBeDisabled();

  await ctl(page, "banner.resume").click();
  await expect(ctl(page, "killswitch.disarm.confirm")).toBeDisabled();
  await ctl(page, "killswitch.disarm.text").fill("resume");
  await expect(ctl(page, "killswitch.disarm.confirm")).toBeDisabled();
  await ctl(page, "killswitch.disarm.cancel").click();
  await expect(banner).toBeVisible();

  await ctl(page, "banner.resume").click();
  await ctl(page, "killswitch.disarm.text").fill("RESUME");
  await ctl(page, "killswitch.disarm.confirm").click();
  await expect(banner).toHaveCount(0);
  await expect(ctl(page, "trading.preview.submit")).toBeEnabled();
});

test("Operations arms and disarms the kill switch", async ({ page }) => {
  await open(page, "/ops");
  await expect(page.locator(".killswitch")).toContainText("Clear: trading enabled");
  await ctl(page, "ops.kill-switch.arm").click();
  await ctl(page, "killswitch.arm.reason").fill("manual review");
  await ctl(page, "killswitch.arm.confirm").click();
  await expect(page.locator(".killswitch")).toContainText("Armed: trading halted");
  await expect(page.locator(".killswitch")).toContainText("manual review");

  await ctl(page, "ops.kill-switch.disarm").click();
  await ctl(page, "killswitch.disarm.text").fill("RESUME");
  await ctl(page, "killswitch.disarm.confirm").click();
  await expect(page.locator(".killswitch")).toContainText("Clear: trading enabled");
});
