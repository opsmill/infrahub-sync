import { useSyncExternalStore } from "react";

export type Theme = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";

export const THEMES: readonly Theme[] = ["system", "light", "dark"];

/** Also read by the script in index.html that paints the first frame. */
const STORAGE_KEY = "infrahub-sync.theme";

const media = window.matchMedia("(prefers-color-scheme: dark)");
const listeners = new Set<() => void>();

function isTheme(value: unknown): value is Theme {
  return typeof value === "string" && (THEMES as readonly string[]).includes(value);
}

function readStored(): Theme {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    return isTheme(value) ? value : "system";
  } catch {
    // Storage throws when the browser blocks site data.
    return "system";
  }
}

let theme = readStored();

function resolve(choice: Theme): ResolvedTheme {
  if (choice !== "system") return choice;
  return media.matches ? "dark" : "light";
}

function paint() {
  const root = document.documentElement;
  const dark = resolve(theme) === "dark";
  if (root.classList.contains("dark") === dark) return;
  // Every surface changes palette in the same frame instead of fading at its own speed.
  root.classList.add("no-transitions");
  root.classList.toggle("dark", dark);
  requestAnimationFrame(() => {
    requestAnimationFrame(() => root.classList.remove("no-transitions"));
  });
}

function update(next: Theme) {
  theme = next;
  paint();
  for (const listener of listeners) listener();
}

media.addEventListener("change", () => update(theme));
window.addEventListener("storage", (event) => {
  if (event.key === STORAGE_KEY || event.key === null) update(readStored());
});
paint();

export function setTheme(next: Theme) {
  try {
    if (next === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, next);
  } catch {
    // The choice still applies to this page, but it is not kept after a reload.
  }
  update(next);
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** The person's choice, the palette painted and the system setting, following other tabs. */
export function useTheme(): { theme: Theme; resolved: ResolvedTheme; system: ResolvedTheme } {
  const current = useSyncExternalStore(subscribe, () => theme);
  const system = useSyncExternalStore(subscribe, () => resolve("system"));
  return { theme: current, resolved: current === "system" ? system : current, system };
}
