import { Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

type Theme = "light" | "dark";
const storageKey = "rutv-admin-theme";
const systemTheme = window.matchMedia("(prefers-color-scheme: dark)");

function storedTheme(): Theme | null {
  try {
    const value = localStorage.getItem(storageKey);
    return value === "light" || value === "dark" ? value : null;
  } catch {
    // Theme controls remain available when browser storage is disabled.
    return null;
  }
}

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document
    .querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "dark" ? "#0b0f17" : "#f8fafc");
}

applyTheme(storedTheme() ?? (systemTheme.matches ? "dark" : "light"));

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(
    () => document.documentElement.dataset.theme as Theme,
  );
  const [hasPreference, setHasPreference] = useState(
    () => storedTheme() !== null,
  );

  useEffect(() => {
    if (hasPreference) return;
    function followSystem(event: MediaQueryListEvent) {
      const next = event.matches ? "dark" : "light";
      applyTheme(next);
      setTheme(next);
    }
    systemTheme.addEventListener("change", followSystem);
    return () => systemTheme.removeEventListener("change", followSystem);
  }, [hasPreference]);

  const label =
    theme === "dark" ? "Switch to light theme" : "Switch to dark theme";
  return (
    <button
      type="button"
      className="icon-button theme-toggle"
      aria-label={label}
      title={label}
      onClick={() => {
        const next = theme === "dark" ? "light" : "dark";
        applyTheme(next);
        setTheme(next);
        setHasPreference(true);
        try {
          localStorage.setItem(storageKey, next);
        } catch {
          // Keep the selected theme for this session when storage is unavailable.
        }
      }}
    >
      {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
