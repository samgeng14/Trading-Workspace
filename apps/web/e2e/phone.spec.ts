import { expect, test } from "@playwright/test";
import { ctl, open } from "./helpers";

// The owner reads the dashboard on a phone over Tailscale: the nameplate stays on top, the status
// line sits under it (mode, clock and halt; then the status segments, which swipe sideways), and
// the sections become a tab bar at the bottom.
test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

const SECTIONS = [
  { id: "nav.positions", path: "/positions", title: "Positions", tab: "Book" },
  { id: "nav.performance", path: "/performance", title: "Performance", tab: "Returns" },
  { id: "nav.risk", path: "/risk", title: "Risk", tab: "Risk" },
  { id: "nav.trading", path: "/trading", title: "Trading", tab: "Trade" },
  { id: "nav.operations", path: "/ops", title: "Operations", tab: "Ops" },
  { id: "nav.overview", path: "/", title: "Command Center", tab: "Command" },
];

test("the sections are a tab bar along the bottom of the screen", async ({ page }) => {
  await open(page);
  const bar = page.getByRole("navigation", { name: "Sections" });
  const box = (await bar.boundingBox())!;
  expect(Math.round(box.y + box.height)).toBe(844);
  expect(box.width).toBe(390);

  for (const s of SECTIONS) {
    const tab = ctl(page, s.id);
    await expect(tab).toBeInViewport();
    await expect(tab).toContainText(s.tab);
    await expect(tab).toHaveAccessibleName(new RegExp(`^${s.title}`));
    await tab.click();
    await expect(page).toHaveURL(new RegExp(`${s.path === "/" ? "/" : s.path}$`));
    await expect(page.locator("h1.page-title")).toHaveText(s.title);
    await expect(tab).toHaveClass(/active/);
  }
});

test("the nameplate on top, then the mode, the clock and the halt", async ({ page }) => {
  await open(page);
  const plate = page.locator(".wordmark");
  await expect(plate).toBeVisible();
  await expect(plate).toHaveText("DV02");
  await expect(page.locator(".dateline")).toContainText("Friday, October 9, 2026");
  // the ears are for wider pages
  await expect(page.locator(".ear-left")).toBeHidden();
  await expect(page.locator(".mode-badge")).toHaveText("Demo");
  await expect(page.locator(".topbar-clock")).toBeVisible();
  const halt = ctl(page, "topbar.kill-switch");
  await expect(halt).toBeVisible();
  await expect(halt).toHaveAccessibleName("Halt trading");
  expect((await plate.boundingBox())!.y).toBeLessThan((await halt.boundingBox())!.y);
  // the status segments scroll sideways inside the line; the page itself never does
  const segs = page.locator(".topbar-segs");
  expect(await segs.evaluate((el) => el.scrollWidth > el.clientWidth)).toBe(true);
  await expect(page.locator(".topbar-session")).toBeVisible();
  await expect(page.locator(".topbar-stream")).toBeAttached();
});

for (const s of SECTIONS) {
  test(`${s.title} fits the phone: no sideways scroll, and the last panel clears the tab bar`, async ({ page }) => {
    await open(page, s.path);
    await expect(page.locator(".main .panel").last()).toBeVisible();
    const sideways = await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    );
    expect(sideways).toBe(0);
    await page.evaluate(() => window.scrollTo(0, document.documentElement.scrollHeight));
    const last = (await page.locator(".main .panel").last().boundingBox())!;
    const bar = (await page.getByRole("navigation", { name: "Sections" }).boundingBox())!;
    expect(last.y + last.height).toBeLessThanOrEqual(bar.y);
  });
}

test("a wide table keeps its symbol column in view while it scrolls sideways", async ({ page }) => {
  await open(page, "/positions");
  const wrap = page.locator("section[aria-label='Book'] .table-wrap");
  const firstSymbol = wrap.locator("tbody tr").first().locator("td").first();
  const before = (await firstSymbol.boundingBox())!;
  await wrap.evaluate((el) => el.scrollBy({ left: 400 }));
  await expect.poll(() => wrap.evaluate((el) => el.scrollLeft)).toBeGreaterThan(300);
  const after = (await firstSymbol.boundingBox())!;
  expect(Math.round(after.x)).toBe(Math.round(before.x));
});
