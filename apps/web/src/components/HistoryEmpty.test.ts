import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { ApiError } from "../api/client";
import { HistoryEmpty, isShortHistory } from "./HistoryEmpty";

describe("isShortHistory", () => {
  it("is the API's 503 for a NAV history too short to draw", () => {
    const err = new ApiError(503, { title: "Not enough NAV history yet", detail: "1 day(s) so far" });
    expect(isShortHistory(err)).toBe(true);
  });
  it("leaves every other failure to the generic error", () => {
    expect(isShortHistory(new ApiError(503, { title: "No price history for SPY" }))).toBe(false);
    expect(isShortHistory(new ApiError(500, { title: "Not enough NAV history yet" }))).toBe(false);
    expect(isShortHistory(new Error("network"))).toBe(false);
  });
});

describe("HistoryEmpty", () => {
  it("says how much history there is and the command that loads the rest", () => {
    const history = { first_date: "2026-10-12", days: 1, complete: false, sources: ["live NAV"] };
    const html = renderToStaticMarkup(createElement(HistoryEmpty, { history }));
    expect(html).toContain("1 day of NAV so far (live NAV)");
    expect(html).toContain("bin/tradeit flex");
  });
  it("still points at the command before the summary has loaded", () => {
    expect(renderToStaticMarkup(createElement(HistoryEmpty, { history: undefined }))).toContain("bin/tradeit flex");
  });
});
