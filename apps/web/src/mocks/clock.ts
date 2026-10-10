// Demo clock. `?demo_now=2026-10-09T14:05:00Z` shifts the simulated time
// (it keeps ticking), so screenshots and e2e tests can show a live session
// on any day. The shift survives reloads within the tab.

const KEY = "demo_now_offset_ms";

function initialOffset(): number {
  if (typeof window === "undefined") return 0;
  const param = new URLSearchParams(window.location.search).get("demo_now");
  if (param) {
    const offset = Date.parse(param) - Date.now();
    if (Number.isFinite(offset)) {
      try {
        sessionStorage.setItem(KEY, String(offset));
      } catch {
        // storage unavailable: the shift lasts until reload
      }
      return offset;
    }
  }
  try {
    return Number(sessionStorage.getItem(KEY) ?? 0) || 0;
  } catch {
    return 0;
  }
}

const offset = initialOffset();

export const now = (): Date => new Date(Date.now() + offset);
