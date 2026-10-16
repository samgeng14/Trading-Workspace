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

test("journal (light) by default; the theme toggle switches to dark and is remembered", async ({ page }) => {
  // the default is the paper, whatever the system prefers
  await page.emulateMedia({ colorScheme: "dark" });
  await open(page);
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const chrome = page.locator('meta[name="theme-color"]');
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  const paper = await bg();
  expect(paper).toBe("rgb(250, 249, 245)");
  await expect(chrome).toHaveAttribute("content", "#faf9f5");

  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await bg()).toBe("rgb(14, 13, 12)");
  await expect(chrome).toHaveAttribute("content", "#0e0d0c");
  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");

  await ctl(page, "topbar.theme").click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
  expect(await bg()).toBe(paper);
});

test("the masthead: the nameplate between its ears, the dateline, then the sections", async ({ page }) => {
  await open(page);
  const head = page.locator(".masthead");
  await expect(head.locator(".wordmark")).toHaveText("DV02");
  await expect(head.locator(".ear-left")).toContainText("Demo edition");
  await expect(head.locator(".ear-left")).toContainText("DU0000000");
  await expect(head.locator(".ear-right")).toContainText("Gateway");
  await expect(head.locator(".ear-right")).toContainText("filled");
  await expect(head.locator(".dateline")).toContainText("Friday, October 9, 2026");
  // the nameplate sits between its ears, over the dateline; the section bar comes next
  const [left, plate, right, date, nav] = await Promise.all(
    [".ear-left", ".wordmark", ".ear-right", ".dateline", "nav[aria-label=Sections]"].map((sel) =>
      page.locator(sel).boundingBox(),
    ),
  );
  expect(left!.x + left!.width).toBeLessThan(plate!.x);
  expect(plate!.x + plate!.width).toBeLessThan(right!.x);
  expect(Math.abs(plate!.x + plate!.width / 2 - 720)).toBeLessThan(2);
  expect(date!.y).toBeGreaterThan(plate!.y + plate!.height - 1);
  expect(nav!.y).toBeGreaterThanOrEqual(date!.y + date!.height - 1);

  // the section bar stays in view as the page scrolls
  await page.mouse.wheel(0, 1500);
  await expect.poll(() => page.evaluate(() => window.scrollY)).toBeGreaterThan(1000);
  expect(Math.round((await page.locator("nav[aria-label=Sections]").boundingBox())!.y)).toBe(0);
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

test("the markets strip shows whole items: one that doesn't fit is hidden, never cut in half", async ({ page }) => {
  await open(page);
  const strip = page.locator(".ticker-items");
  await expect(strip.locator(".ticker-item").first()).toBeVisible();
  await expect(strip.locator(".ticker-tag").first()).toHaveText(/Gainer|Loser/);
  await expect(page.locator(".ticker-note")).toHaveText("Delayed 15 min");
  for (const width of [1440, 1024]) {
    await page.setViewportSize({ width, height: 900 });
    const cut = await strip.evaluate((ul) => {
      const box = ul.getBoundingClientRect();
      return Array.from(ul.querySelectorAll(".ticker-item"))
        .map((li) => li.getBoundingClientRect())
        .filter((r) => r.top < box.bottom && r.right > box.right + 1).length;
    });
    expect(cut, `${width}px`).toBe(0);
  }
});
