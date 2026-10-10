import { expect, type Locator, type Page } from "@playwright/test";

/** Friday Oct 9 2026, 14:12 ET: market open, before the 15:40 MOC submission. */
export const MARKET_OPEN = "2026-10-09T18:12:00Z";
/** Thursday Oct 1 2026, 17:00 ET: after a monthly rebalance filled at the close. */
export const AFTER_REBALANCE = "2026-10-01T21:00:00Z";

export async function open(page: Page, path = "/", now = MARKET_OPEN) {
  await page.goto(`${path}${path.includes("?") ? "&" : "?"}demo_now=${now}`);
  await expect(page.locator(".wordmark")).toHaveText("DV02 Fund");
  await expect(page.locator(".mode-badge")).toBeVisible(); // status loaded
}

/** A control by its data-control id. */
export const ctl = (page: Page, id: string): Locator => page.locator(`[data-control="${id}"]`);

/** Declares which registry patterns a test exercises (read by controls.spec.ts). */
export function covers(...patterns: string[]): void {
  void patterns;
}
