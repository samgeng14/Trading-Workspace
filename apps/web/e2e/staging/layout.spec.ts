import { expect, test, type Page } from "@playwright/test";

// Layout on real data shapes: the staging API's fresh scenario (playwright.staging.config.ts,
// `npm run e2e:staging`) has the paper account's long labels, 37 holdings, a missed daily run and
// one day of NAV history, which the demo's tidy data never shows. The default config finds this
// file too and defines nothing from it.
const STAGING = process.env.DV02_STAGING_E2E === "1";

const PAGES = [
  { path: "/", title: "Command Center", ready: '[aria-label="Schedule"] .schedule-row' },
  { path: "/positions", title: "Positions", ready: '[aria-label="Book"] tbody tr' },
  { path: "/performance", title: "Performance", ready: ".history-empty" },
  { path: "/risk", title: "Risk", ready: '[aria-label="Limits"] .meter-row' },
  { path: "/trading", title: "Trading", ready: '[aria-label="Pre-trade checks"] li' },
  { path: "/ops", title: "Operations", ready: '[aria-label="Schedule"] .schedule-row' },
];
const WIDTHS = [1440, 390];
const THEMES = ["light", "dark"] as const;

// the real schedule's wording (services/api/dv02_api/runs.py)
const DAILY_RUN = "Daily run: data, targets, rebalance if drift > 5%; orders go market-on-close";
const MISSED = "no run recorded yet today; is the Mac awake and the LaunchAgent installed?";

async function openPage(page: Page, path: string, ready: string) {
  await page.goto(path);
  await expect(page.locator("h1.page-title")).toBeVisible();
  await expect(page.locator(ready).first()).toBeVisible({ timeout: 15_000 });
  await page.evaluate(() => document.fonts.ready);
}

/** Horizontal overflow, as "where: what" lines (empty when everything fits). */
function overflow(page: Page): Promise<string[]> {
  return page.evaluate(() => {
    const out: string[] = [];
    const slack = 1;
    const name = (el: Element) =>
      el.tagName.toLowerCase() +
      (el.className && typeof el.className === "string" ? `.${el.className.trim().split(/\s+/).join(".")}` : "");
    const doc = document.documentElement;
    if (doc.scrollWidth > doc.clientWidth + slack)
      out.push(`document scrolls sideways: ${doc.scrollWidth} > ${doc.clientWidth}`);
    const main = document.querySelector<HTMLElement>(".main");
    if (main && main.scrollWidth > main.clientWidth + slack)
      out.push(`.main scrolls sideways: ${main.scrollWidth} > ${main.clientWidth}`);
    const area = (main ?? doc).getBoundingClientRect();

    const clips = (el: Element) => getComputedStyle(el).overflowX !== "visible";
    for (const panel of Array.from(document.querySelectorAll<HTMLElement>(".main .panel, .main .page-head"))) {
      const box = panel.getBoundingClientRect();
      if (!box.width) continue;
      const label = panel.getAttribute("aria-label") ?? name(panel);
      if (box.left < area.left - slack || box.right > area.right + slack)
        out.push(`${label}: the panel runs past the page (${Math.round(box.left)}-${Math.round(box.right)})`);
      // Text inside a box that clips or scrolls (a wide table, an ellipsis) is that box's; the box must fit.
      const boxes = new Set<Element>();
      const walker = document.createTreeWalker(panel, NodeFilter.SHOW_TEXT);
      for (let n = walker.nextNode(); n; n = walker.nextNode()) {
        const text = n.textContent?.trim();
        const el = n.parentElement;
        if (!text || !el || !el.checkVisibility({ checkOpacity: true, checkVisibilityCSS: true })) continue;
        let clipper: Element | null = null;
        for (let a: Element | null = el; a && a !== panel; a = a.parentElement) if (clips(a)) clipper = a;
        if (clipper) {
          boxes.add(clipper);
          continue;
        }
        const range = document.createRange();
        range.selectNodeContents(n);
        const rects = el instanceof SVGElement ? [el.getBoundingClientRect()] : Array.from(range.getClientRects());
        for (const r of rects) {
          if (r.width && (r.left < box.left - slack || r.right > box.right + slack)) {
            out.push(
              `${label}: "${text.slice(0, 60)}" runs past the panel (${Math.round(r.left)}-${Math.round(r.right)} vs ${Math.round(box.left)}-${Math.round(box.right)})`,
            );
            break;
          }
        }
      }
      for (const b of boxes) {
        const r = b.getBoundingClientRect();
        if (r.width && (r.left < box.left - slack || r.right > box.right + slack))
          out.push(`${label}: ${name(b)} runs past the panel (${Math.round(r.left)}-${Math.round(r.right)})`);
      }
    }
    return out;
  });
}

if (STAGING) {
  for (const theme of THEMES) {
    for (const width of WIDTHS) {
      test.describe(`${theme} theme at ${width}px`, () => {
        test.use({ viewport: { width, height: 900 } });
        test.beforeEach(async ({ page }) => {
          await page.addInitScript((t) => localStorage.setItem("dv02.theme", t), theme);
        });

        for (const p of PAGES) {
          test(`${p.title} fits its real labels`, async ({ page }) => {
            await openPage(page, p.path, p.ready);
            await expect(page.locator("html")).toHaveAttribute("data-theme", theme);
            await expect(page.locator("h1.page-title")).toHaveText(p.title);
            expect(await overflow(page)).toEqual([]);
          });
        }

        for (const p of PAGES.filter((x) => x.path === "/" || x.path === "/ops")) {
          test(`${p.title}: the Schedule rows hold the real daily-run label and its failure`, async ({ page }) => {
            await openPage(page, p.path, p.ready);
            const panel = page.locator('section[aria-label="Schedule"]');
            const row = panel.locator(".schedule-row").first();
            await expect(row).toHaveAttribute("data-status", "failed");
            await expect(row.locator(".schedule-label")).toHaveText(DAILY_RUN);
            await expect(row.locator(".schedule-status")).toHaveText(MISSED);
            const fit = await row.evaluate((el) => {
              const r = el.getBoundingClientRect();
              const panelBox = el.closest("section")!.getBoundingClientRect();
              const parts = Array.from(el.querySelectorAll(".schedule-time, .schedule-label, .schedule-status")).map(
                (c) => c.getBoundingClientRect(),
              );
              const label = el.querySelector(".schedule-label")!;
              const lines = label.getBoundingClientRect().height / parseFloat(getComputedStyle(label).lineHeight);
              return {
                inRow: parts.every((c) => c.left >= r.left - 1 && c.right <= r.right + 1),
                inPanel: r.left >= panelBox.left - 1 && r.right <= panelBox.right + 1,
                labelLines: Math.round(lines),
              };
            });
            expect(fit.inRow, "time, label and status stay inside the row").toBe(true);
            expect(fit.inPanel, "the row stays inside the Schedule panel").toBe(true);
            // crushed, the label used to break after every word or two
            expect(fit.labelLines, "the label keeps whole lines").toBeLessThanOrEqual(3);
          });
        }

        test("the equity panels say there is no NAV history yet", async ({ page }) => {
          await openPage(page, "/", '[aria-label="Schedule"] .schedule-row');
          const overview = page.locator('section[aria-label="Since inception"] .history-empty');
          await expect(overview).toBeVisible();
          await expect(overview).toContainText("No NAV history yet");
          await expect(overview).toContainText("1 day of NAV so far");
          await openPage(page, "/performance", ".history-empty");
          await expect(page.locator(".history-empty")).toContainText("bin/tradeit flex");
        });
      });
    }
  }
}
