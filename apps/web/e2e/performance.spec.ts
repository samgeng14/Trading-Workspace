import { expect, type Page, test } from "@playwright/test";
import { covers, ctl, open } from "./helpers";

test("period presets change the window", async ({ page }) => {
  covers("performance.range.*");
  await open(page, "/performance");
  const sub = page.locator(".page-sub");
  await expect(sub).toContainText("Oct 15, 2025");
  const expected: Record<string, RegExp> = {
    "1W": /Oct \d+, 2026/,
    MTD: /Sep 30, 2026/,
    "1M": /Sep \d+, 2026/,
    "3M": /Jul \d+, 2026/,
    "6M": /Apr \d+, 2026/,
    YTD: /Dec 31, 2025/,
    "1Y": /Oct 15, 2025/,
    ITD: /Oct 15, 2025/,
  };
  for (const [range, start] of Object.entries(expected)) {
    await ctl(page, `performance.range.${range}`).click();
    await expect(ctl(page, `performance.range.${range}`)).toHaveAttribute("aria-pressed", "true");
    await expect(sub).toContainText(start);
  }
});

test("benchmark switch relabels everything", async ({ page }) => {
  await open(page, "/performance");
  await ctl(page, "performance.benchmark").selectOption("QQQ");
  await expect(page.locator("section[aria-label='Return vs Nasdaq-100']")).toBeVisible();
  await expect(page.locator(".summary-strip")).toContainText("Beta to Nasdaq-100");
  await ctl(page, "performance.benchmark").selectOption("6040");
  await expect(page.locator("section[aria-label='Return vs 60/40']")).toBeVisible();
});

test("monthly grid switches between portfolio, benchmark and excess", async ({ page }) => {
  covers("performance.monthly.*");
  await open(page, "/performance");
  const panel = page.locator("section[aria-label='Monthly returns']");
  await expect(panel.locator(".panel-meta")).toContainText("portfolio");
  const firstCell = () => panel.locator("tbody tr").last().locator("td").nth(1).innerText();
  const port = await firstCell();
  await ctl(page, "performance.monthly.benchmark").click();
  await expect(panel.locator(".panel-meta")).toContainText("S&P 500");
  expect(await firstCell()).not.toBe(port);
  await ctl(page, "performance.monthly.excess").click();
  await expect(panel.locator(".panel-meta")).toContainText("portfolio minus S&P 500");
  await ctl(page, "performance.monthly.portfolio").click();
  expect(await firstCell()).toBe(port);
});

test("growth and drawdown charts answer the pointer and the keyboard", async ({ page }) => {
  await open(page, "/performance");
  const growth = ctl(page, "performance.growth.plot");
  const box = (await growth.boundingBox())!;
  await growth.hover({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
  const tip = page.locator("section[aria-label^='Return vs'] .tooltip");
  await expect(tip).toContainText("Portfolio");
  await expect(tip).toContainText("S&P 500");
  await expect(tip).toContainText(/[+−]\d+\.\d{2}%/);

  const dd = ctl(page, "performance.drawdown.plot");
  await dd.focus();
  await page.keyboard.press("End");
  await expect(page.locator("section[aria-label='Drawdown'] .tooltip")).toContainText("Drawdown");
  await page.keyboard.press("Escape");
  await expect(page.locator("section[aria-label='Drawdown'] .tooltip")).toHaveCount(0);
});

/** Smallest gap, in px, between any tick label and the first point of any line in a chart. */
async function firstPointClearance(page: Page, control: string): Promise<number> {
  return ctl(page, control).evaluate((svg) => {
    const labels = Array.from(svg.querySelectorAll("text.tick"), (t) => t.getBoundingClientRect());
    const lines = Array.from(svg.querySelectorAll<SVGPathElement>("path[stroke]"));
    let min = Infinity;
    for (const line of lines) {
      const p = line.getPointAtLength(0);
      const m = line.getScreenCTM()!;
      const x = p.x * m.a + p.y * m.c + m.e;
      const y = p.x * m.b + p.y * m.d + m.f;
      for (const r of labels) {
        const dx = Math.max(r.left - x, 0, x - r.right);
        const dy = Math.max(r.top - y, 0, y - r.bottom);
        min = Math.min(min, Math.hypot(dx, dy));
      }
    }
    return min;
  });
}

test("equity curve: cumulative return by default, then growth of $100 and NAV in dollars", async ({ page }) => {
  covers("performance.growth.mode.*");
  await open(page, "/performance");
  // the broker-app view: return since the start, a 0% line, a big total-return figure
  await expect(ctl(page, "performance.growth.mode.return")).toHaveAttribute("aria-pressed", "true");
  const ret = page.locator("section[aria-label='Return vs S&P 500']");
  await expect(ret.locator("text.tick").filter({ hasText: /^0%$/ })).toHaveCount(1);
  await expect(ret.locator(".equity-headline-caption")).toHaveText("Total return since inception");
  await expect(ret.locator(".equity-headline-value")).toHaveText(/^[+−]\d+\.\d{2}%$/);
  await expect(ret.locator("path.fill-up")).toHaveCount(1); // the portfolio's wash only, not the benchmark's
  await ctl(page, "performance.range.1W").click();
  await expect(ret.locator(".equity-headline-caption")).toHaveText("Return over the last week");
  await ctl(page, "performance.range.ITD").click();

  await ctl(page, "performance.growth.mode.growth").click();
  const growth = page.locator("section[aria-label='Growth of $100 vs S&P 500']");
  await expect(growth.locator("text.tick").filter({ hasText: /^100$/ })).toHaveCount(1);

  await ctl(page, "performance.growth.mode.nav").click();
  await expect(ctl(page, "performance.growth.mode.nav")).toHaveAttribute("aria-pressed", "true");
  const nav = page.locator("section[aria-label='NAV vs S&P 500']");
  await expect(nav.locator(".legend")).toContainText("S&P 500, from the same starting NAV");
  await expect(
    nav
      .locator("text.tick")
      .filter({ hasText: /^\$\d\.\d{2}M$/ })
      .first(),
  ).toBeVisible();
  const plot = ctl(page, "performance.growth.plot");
  const box = (await plot.boundingBox())!;
  await plot.hover({ position: { x: box.width * 0.5, y: box.height * 0.5 } });
  await expect(nav.locator(".tooltip")).toContainText(/\$(\d{1,3},)?\d{3},\d{3}/);
  await expect(nav.locator(".tooltip")).toContainText("Portfolio");

  await ctl(page, "performance.growth.view-table").click();
  await expect(nav.locator("thead")).toContainText("Portfolio NAV");
  await expect(nav.locator("tbody tr").first().locator("td").nth(1)).toHaveText(/^\$(\d{1,3},)?\d{3},\d{3}$/);
  await ctl(page, "performance.growth.view-chart").click();

  await ctl(page, "performance.growth.mode.return").click();
  await expect(ret).toBeVisible();
});

test("the overview equity panel has the same switch", async ({ page }) => {
  covers("overview.performance.mode.*");
  await open(page);
  const panel = page.locator("section[aria-label='Since inception vs S&P 500']");
  await expect(panel.locator(".panel-meta")).toContainText("cumulative return");
  await expect(panel.locator(".equity-headline-caption")).toHaveText("Total return since inception");
  await ctl(page, "overview.performance.mode.growth").click();
  await expect(panel.locator(".panel-meta")).toContainText("growth of $100");
  await ctl(page, "overview.performance.mode.nav").click();
  await expect(panel.locator(".panel-meta")).toContainText("NAV in dollars");
  await expect(panel.locator(".legend")).toContainText("from the same starting NAV");
  await expect(panel.locator(".end-value").first()).toHaveText(/^\$(\d{1,3},)?\d{3},\d{3}$/);
  await ctl(page, "overview.performance.mode.return").click();
  await expect(panel.locator(".panel-meta")).toContainText("cumulative return");
});

for (const width of [375, 1440]) {
  test(`tick labels stay clear of the lines' first points at ${width}px`, async ({ page }) => {
    for (const [path, prefix, other] of [
      ["/performance", "performance.growth", "performance.drawdown.plot"],
      ["/", "overview.performance", "overview.intraday.plot"],
    ] as const) {
      await page.setViewportSize({ width: 1440, height: 900 });
      await open(page, path); // waits for the top bar, which a phone layout hides
      await page.setViewportSize({ width, height: 900 });
      for (const mode of ["growth", "nav"]) {
        await ctl(page, `${prefix}.mode.${mode}`).click();
        // a hover dot is r 4 with a 2px ring
        expect(await firstPointClearance(page, `${prefix}.plot`)).toBeGreaterThan(5);
      }
      expect(await firstPointClearance(page, other)).toBeGreaterThan(5);
    }
  });
}

test("y labels never repeat, even over a quiet month", async ({ page }) => {
  await open(page, "/performance");
  // the demo's 1M drawdown runs 0 to -3% in half-point ticks
  await ctl(page, "performance.range.1M").click();
  const yLabels = (control: string) => ctl(page, control).locator("text.tick[x^='-']");
  await expect(yLabels("performance.drawdown.plot").filter({ hasText: /^−2\.5%$/ })).toHaveCount(1);
  for (const mode of ["growth", "nav"]) {
    await ctl(page, `performance.growth.mode.${mode}`).click();
    for (const control of ["performance.growth.plot", "performance.drawdown.plot"]) {
      const labels = await yLabels(control).allTextContents();
      expect(labels.length, control).toBeGreaterThan(2);
      expect(new Set(labels).size, labels.join(" ")).toBe(labels.length);
    }
  }
});
