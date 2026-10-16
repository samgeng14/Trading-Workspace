import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
// A new key when the Journal theme became the default (it was dark before), so an
// old pick of dark doesn't keep anyone off the Journal.
export const THEME_KEY = "dv02.theme";
const CHROME = { dark: "#0e0d0c", light: "#faf9f5" } as const;

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
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
    // the phone's browser bar takes the page colour
    document.querySelector<HTMLMetaElement>('meta[name="theme-color"]')?.setAttribute("content", CHROME[theme]);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((t) => {
      const next: Theme = t === "dark" ? "light" : "dark";
      try {
        localStorage.setItem(THEME_KEY, next);
      } catch {
        // not persisted; still applied for this page
      }
      return next;
    });
  }, []);

  return [theme, toggle];
}
