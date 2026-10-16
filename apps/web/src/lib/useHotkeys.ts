import { useEffect, useRef } from "react";

/**
 * Two-key "g then x" shortcuts. Ignored while typing in a field, with a dialog
 * open, or when a modifier is held, so they never fight the browser or a form.
 *
 * The listener is registered once; the map is read from a ref. Re-registering
 * on every navigation would drop a "g" pressed just before the re-render.
 */
export function useGoHotkeys(map: Record<string, () => void>) {
  const mapRef = useRef(map);
  useEffect(() => {
    mapRef.current = map;
  }, [map]);

  useEffect(() => {
    let armedUntil = 0;
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
      if (document.querySelector("[role=dialog]")) return;
      const key = e.key.toLowerCase();
      if (key === "g") {
        armedUntil = Date.now() + 1200;
        return;
      }
      const go = mapRef.current[key];
      if (Date.now() < armedUntil && go) {
        armedUntil = 0;
        e.preventDefault();
        go();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}
