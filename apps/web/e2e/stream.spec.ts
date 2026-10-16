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

test("the status strip fits every desktop width: all five system lights, names from 1601px, the stream from 1440px", async ({
  page,
}) => {
  await open(page);
  const lights = page.locator(".topbar-health .badge");
  const stream = page.locator(".topbar-stream");
  const cases = [
    [1024, false, false],
    [1180, false, false],
    [1280, false, false],
    [1366, false, false],
    [1440, true, false],
    [1600, true, false],
    [1601, true, true],
    [1920, true, true],
  ] as const;
  for (const [width, streamShown, named] of cases) {
    await page.setViewportSize({ width, height: 900 });
    await expect(lights.filter({ visible: true }), `${width}px`).toHaveCount(5);
    await expect(stream, `${width}px`).toBeVisible({ visible: streamShown });
    // a short name stays in the light's text either way; below 1601px it is only read out
    const nameWidth = await lights
      .first()
      .locator(".badge-text")
      .evaluate((el) => el.getBoundingClientRect().width);
    expect(nameWidth > 1, `${width}px: names shown`).toBe(named);
    await expect(page.locator(".topbar-health .health-count"), `${width}px`).toBeVisible({ visible: !named });
    const clipped = await page.locator(".topbar-segs").evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(clipped, `${width}px`).toBe(0);
    const bar = await page.locator(".topbar").evaluate((el) => el.scrollWidth - el.clientWidth);
    expect(bar, `${width}px`).toBe(0);
  }
});
