import { createElement, type ComponentProps } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { Meter } from "./bits";

/** The bar's fill as a percentage, and the status it is coloured by. */
function bar(props: Omit<ComponentProps<typeof Meter>, "label" | "valueText" | "limitText">) {
  const html = renderToStaticMarkup(createElement(Meter, { label: "x", valueText: "v", limitText: "l", ...props }));
  const fill = html.match(/class="meter-fill" data-status="(\w+)" style="width:([\d.]+)%"/);
  if (!fill) throw new Error(`no meter fill in ${html}`);
  return { status: fill[1], width: Number(fill[2]) };
}

describe("Meter", () => {
  it("fills a ceiling by the share of the limit used", () => {
    expect(bar({ value: 1.2, limit: 1.5, status: "ok" })).toEqual({ status: "ok", width: 80 });
    expect(bar({ value: 0.155, limit: 0.12, status: "breach" })).toEqual({ status: "breach", width: 100 });
  });

  it("fills a loss limit by the loss, and stays empty on a gain", () => {
    expect(bar({ value: -0.04, limit: -0.05, status: "warning" }).width).toBeCloseTo(80);
    expect(bar({ value: 0.006, limit: -0.02, status: "ok" }).width).toBe(0);
  });

  it("fills a floor as the value falls toward it", () => {
    expect(bar({ value: 0.004, limit: 0, status: "ok" }).width).toBeCloseTo(99.6);
    expect(bar({ value: 0.4, limit: 0, status: "ok" }).width).toBeCloseTo(60);
    expect(bar({ value: 1, limit: 0, status: "ok" }).width).toBe(0);
  });

  it("shows a breached floor as a full bar in the breach colour", () => {
    expect(bar({ value: -0.0004, limit: 0, status: "breach" })).toEqual({ status: "breach", width: 100 });
    // a floor above zero has to be named; a breach still fills the bar
    expect(bar({ value: 0.06, limit: 0.05, status: "ok", floor: true }).width).toBeCloseTo((0.94 / 0.95) * 100);
    expect(bar({ value: 0.03, limit: 0.05, status: "breach" })).toEqual({ status: "breach", width: 100 });
  });

  it("gives the value and limit to assistive tech", () => {
    const html = renderToStaticMarkup(
      createElement(Meter, {
        label: "Cash (no margin loan)",
        value: -0.0004,
        limit: 0,
        status: "breach",
        valueText: "−0.04%",
        limitText: "0.0%",
      }),
    );
    expect(html).toContain('aria-valuenow="100"');
    expect(html).toContain('aria-valuetext="−0.04%, limit 0.0%"');
  });

  it("prints the limit beside the value and the share used under the bar", () => {
    const html = renderToStaticMarkup(
      createElement(Meter, {
        label: "Gross exposure",
        value: 0.963,
        limit: 1,
        status: "ok",
        valueText: "96.3%",
        limitText: "100.0%",
      }),
    );
    expect(html).toMatch(/class="meter-value num">96\.3%<span class="meter-limit"> \/ 100\.0%<\/span>/);
    expect(html).toContain('<span class="num meter-used">96% used</span>');
  });
});
