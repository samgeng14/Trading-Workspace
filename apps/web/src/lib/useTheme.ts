import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
// A new key, so a pick saved before the Journal theme became the default
// (when dark was the default) doesn't keep anyone on dark.
const KEY = "dv02.theme";

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The Journal (light) theme unless the viewer picked dark; the pick is remembered per browser. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => stored() ?? "light");

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(KEY, next);
      } catch {
        // not persisted; still applied for this page
      }
      return next;
    });
  }, []);

  return [theme, toggle];
}
