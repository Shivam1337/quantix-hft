import { useEffect, useState } from "react";

export type ThemeMode = "dark" | "light" | "system";

export function useTheme() {
  const [theme, setTheme] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem("theme_mode") as ThemeMode | null;
    return saved === "dark" || saved === "light" || saved === "system" ? saved : "system";
  });

  const [resolvedTheme, setResolvedTheme] = useState<"dark" | "light">("dark");

  useEffect(() => {
    const mediaQuery = window.matchMedia("(prefers-color-scheme: dark)");

    const applyTheme = () => {
      let isDark = true;
      if (theme === "system") {
        isDark = mediaQuery.matches;
      } else {
        isDark = theme === "dark";
      }

      const active = isDark ? "dark" : "light";
      setResolvedTheme(active);

      const root = document.documentElement;
      if (isDark) {
        root.classList.add("dark");
        root.classList.remove("light");
      } else {
        root.classList.add("light");
        root.classList.remove("dark");
      }
      localStorage.setItem("theme_mode", theme);
    };

    applyTheme();

    const listener = () => {
      if (theme === "system") applyTheme();
    };

    mediaQuery.addEventListener("change", listener);
    return () => mediaQuery.removeEventListener("change", listener);
  }, [theme]);

  return { theme, setTheme, resolvedTheme };
}
