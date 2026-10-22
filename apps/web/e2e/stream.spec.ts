import { expect, test } from "@playwright/test";
import { open } from "./helpers";

test("the demo build polls and never opens the live stream", async ({ page }) => {
  const sockets: string[] = [];
  page.on("websocket", (ws) => sockets.push(ws.url()));
  await open(page);
  const seg = page.locator(".topbar-stream");
  await expect(seg).toContainText("Stream");
  await expect(seg).toContainText("Polling");
  await expect(seg.locator("[title]")).toHaveAttribute("title", /simulated backend is polled every 5 s/);
  await page.waitForTimeout(1_500); // the app would have connected by now
  expect(sockets.filter((url) => url.includes("/api/v1/stream"))).toEqual([]);
});

test("the status strip shows whole segments: all health badges where they fit, the stream where it fits", async ({
  page,
}) => {
  await open(page);
  const badges = page.locator(".topbar-health .badge").filter({ visible: true });
  const stream = page.locator(".topbar-stream");
  const cases = [
    [1280, 2, false],
    [1366, 5, false],
    [1400, 5, false],
    [1440, 2, true],
    [1600, 2, true],
    [1601, 5, true],
    [1920, 5, true],
  ] as const;
  for (const [width, shown, streamShown] of cases) {
    await page.setViewportSize({ width, height: 900 });
    await expect(badges, `${width}px`).toHaveCount(shown);
    await expect(stream, `${width}px`).toBeVisible({ visible: streamShown });
    const clipped = await page.locator(".topbar-segs").evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(clipped, `${width}px`).toBe(0);
  }
});
