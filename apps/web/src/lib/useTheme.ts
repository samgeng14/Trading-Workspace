import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";
// A new key with the Terminal theme, so a pick saved while the Journal (light) theme was the
// default doesn't keep anyone off the new dark default.
export const THEME_KEY = "dv02.ui-theme";
const CHROME = { dark: "#05070b", light: "#eef1f6" } as const;

function stored(): Theme | null {
  try {
    const v = localStorage.getItem(THEME_KEY);
    return v === "light" || v === "dark" ? v : null;
  } catch {
    return null;
  }
}

/** The dark Terminal theme unless the viewer picked light; the pick is remembered per browser. */
export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(() => stored() ?? "dark");

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
