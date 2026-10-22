import { readdirSync, readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { expect, type Page, test } from "@playwright/test";
import { CONTROLS, isRegistered } from "./controls";
import { ctl, open } from "./helpers";

const here = dirname(fileURLToPath(import.meta.url));

async function rendered(page: Page): Promise<string[]> {
  return page.locator("[data-control]").evaluateAll((els) => els.map((e) => e.getAttribute("data-control")!));
}

test("every rendered control is in the registry", async ({ page }) => {
  const seen = new Set<string>();
  const collect = async () => (await rendered(page)).forEach((id) => seen.add(id));

  for (const path of ["/", "/positions", "/performance", "/risk", "/trading", "/ops"]) {
    await open(page, path);
    await collect();
  }
  // dialogs and armed state
  await open(page, "/trading");
  await ctl(page, "trading.preview.submit").click();
  await collect();
  await ctl(page, "rebalance.submit.cancel").click();
  await ctl(page, "topbar.kill-switch").click();
  await collect();
  await ctl(page, "killswitch.arm.reason").fill("registry check");
  await ctl(page, "killswitch.arm.confirm").click();
  await ctl(page, "banner.resume").click();
  await collect();
  await ctl(page, "killswitch.disarm.cancel").click();
  await open(page, "/ops");
  await collect();

  const unregistered = [...seen].filter((id) => !isRegistered(id));
  expect(unregistered, "add these to e2e/controls.ts and give each a test").toEqual([]);
  expect(seen.size).toBeGreaterThan(60);
});

test("every registered control is exercised by a spec", () => {
  const specs = readdirSync(here)
    .filter((f) => f.endsWith(".spec.ts") && f !== "controls.spec.ts")
    .map((f) => readFileSync(join(here, f), "utf8"))
    .join("\n");
  const missing = CONTROLS.filter((id) => !specs.includes(`"${id}"`) && !specs.includes(`\`${id}\``));
  expect(missing, "write a test that uses ctl() or covers() with these ids").toEqual([]);
});
