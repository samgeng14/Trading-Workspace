import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

const SECTIONS = [
  { id: "nav.positions", path: "/positions", title: "Positions", key: "p" },
  { id: "nav.performance", path: "/performance", title: "Performance", key: "f" },
  { id: "nav.risk", path: "/risk", title: "Risk", key: "r" },
  { id: "nav.trading", path: "/trading", title: "Trading", key: "t" },
  { id: "nav.operations", path: "/ops", title: "Operations", key: "s" },
];

test("side navigation reaches every section and back to the Command Center", async ({ page }) => {
  await open(page);
  for (const s of SECTIONS) {
    await ctl(page, s.id).click();
    await expect(page).toHaveURL(new RegExp(`${s.path}$`));
    await expect(page.locator("h1.page-title")).toHaveText(s.title);
    await expect(ctl(page, s.id)).toHaveClass(/active/);
  }
  await ctl(page, "nav.overview").click();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.locator("h1.page-title")).toHaveText("Command Center");
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
  await page.keyboard.press("c");
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

test("the dark Terminal theme by default; the toggle switches to light and is remembered", async ({ page }) => {
  // the default is the owner's pick, not the system's
  await page.emulateMedia({ colorScheme: "light" });
  await open(page);
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const chrome = page.locator('meta[name="theme-color"]');
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  const night = await bg();
  expect(night).toBe("rgb(5, 7, 11)");
  await expect(chrome).toHaveAttribute("content", "#05070b");

  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await bg()).toBe("rgb(238, 241, 246)");
  await expect(chrome).toHaveAttribute("content", "#eef1f6");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");

  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await bg()).toBe(night);
});

test("a theme saved while the Journal theme was the default doesn't override the new one", async ({ page }) => {
  await page.addInitScript(() => localStorage.setItem("dv02.theme", "light"));
  await open(page);
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
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

test("the market chip shows how far through the session the exchange is", async ({ page }) => {
  await open(page); // Friday 14:12 ET: 282 of the session's 390 minutes
  const bar = page.locator(".topbar-session .seg-progress > span");
  const width = async () =>
    bar.evaluate((el) => el.getBoundingClientRect().width / el.parentElement!.getBoundingClientRect().width);
  expect(await width()).toBeGreaterThan(0.71);
  expect(await width()).toBeLessThan(0.75);
  await open(page, "/", "2026-10-10T16:00:00Z"); // Saturday: no session, no bar
  await expect(page.locator(".topbar-session")).toContainText("Market closed");
  await expect(page.locator(".seg-progress")).toHaveCount(0);
});

test("the ticker tape runs two copies of the same list, the second hidden from screen readers", async ({ page }) => {
  await open(page);
  const lists = page.locator(".ticker .ticker-items");
  await expect(lists).toHaveCount(2);
  await expect(lists.nth(0)).not.toHaveAttribute("aria-hidden", "true");
  await expect(lists.nth(1)).toHaveAttribute("aria-hidden", "true");
  await expect(lists.nth(0).locator(".ticker-item").first()).toBeVisible();
  expect(await lists.nth(1).innerText()).toBe(await lists.nth(0).innerText());
  await expect(page.locator(".ticker-note")).toHaveText("Delayed 15 min");

  // with reduced motion the tape stands still and shows one copy
  await page.emulateMedia({ reducedMotion: "reduce" });
  await expect(lists.nth(1)).toBeHidden();
  const moving = await page
    .locator(".ticker-tape")
    .evaluate((el) => getComputedStyle(el).animationName !== "none" && getComputedStyle(el).animationDuration !== "0s");
  expect(moving).toBe(false);
});
