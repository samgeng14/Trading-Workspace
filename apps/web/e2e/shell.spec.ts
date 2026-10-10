import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

const SECTIONS = [
  { id: "nav.positions", path: "/positions", title: "Positions", key: "p" },
  { id: "nav.performance", path: "/performance", title: "Performance", key: "f" },
  { id: "nav.risk", path: "/risk", title: "Risk", key: "r" },
  { id: "nav.trading", path: "/trading", title: "Trading", key: "t" },
  { id: "nav.operations", path: "/ops", title: "Operations", key: "s" },
];

test("side navigation reaches every section and back to Overview", async ({ page }) => {
  await open(page);
  for (const s of SECTIONS) {
    await ctl(page, s.id).click();
    await expect(page).toHaveURL(new RegExp(`${s.path}$`));
    await expect(page.locator("h1.page-title")).toHaveText(s.title);
    await expect(ctl(page, s.id)).toHaveClass(/active/);
  }
  await ctl(page, "nav.overview").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByTestId("nav")).toBeVisible();
});

test("g-then-key shortcuts navigate, and are ignored while typing", async ({ page }) => {
  await open(page);
  for (const s of SECTIONS) {
    await page.keyboard.press("g");
    await page.keyboard.press(s.key);
    await expect(page).toHaveURL(new RegExp(`${s.path}$`));
  }
  await page.keyboard.press("g");
  await page.keyboard.press("o");
  await expect(page).toHaveURL(/\/$/);

  await ctl(page, "nav.positions").click();
  await ctl(page, "positions.search").pressSequentially("gr");
  await expect(page).toHaveURL(/\/positions$/);
});

test("system health opens Operations", async ({ page }) => {
  await open(page);
  await expect(ctl(page, "topbar.health")).toContainText("IBKR");
  await ctl(page, "topbar.health").click();
  await expect(page).toHaveURL(/\/ops$/);
});

test("theme toggle switches and is remembered", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await open(page);
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const light = await bg();
  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await bg()).not.toBe(light);
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await bg()).toBe(light);
});

test("market session and clock follow the exchange", async ({ page }) => {
  await open(page);
  await expect(page.locator(".topbar-session")).toContainText("Market open");
  await expect(page.locator(".topbar-session")).toContainText("Closes in 1h 4");
  await expect(page.locator(".topbar-clock")).toContainText("14:1");
  await open(page, "/", "2026-10-10T16:00:00Z");
  await expect(page.locator(".topbar-session")).toContainText("Market closed");
  await expect(page.locator(".topbar-session")).toContainText("Opens Mon 09:30 ET");
});
