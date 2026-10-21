/** Light, dark or System, kept in localStorage under the same key as Django's theme.js, so both apps agree. Light until chosen. */
export type ThemeChoice = "light" | "dark" | "auto";

export function storedTheme(): ThemeChoice {
  try {
    const v = localStorage.getItem("theme");
    return v === "dark" || v === "auto" ? v : "light";
  } catch {
    return "light";
  }
}

export function applyTheme(choice: ThemeChoice) {
  const mode = choice === "auto" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : choice;
  document.documentElement.setAttribute("data-bs-theme", mode);
  try {
    localStorage.setItem("theme", choice);
  } catch {
    /* private mode: the choice lasts until the page closes */
  }
}

/** Runs in <head> before the first paint, so a dark page is not drawn white first. */
export const themeBootScript = `(function(){try{var v=localStorage.getItem("theme");var d=v==="dark"||(v==="auto"&&window.matchMedia("(prefers-color-scheme: dark)").matches);document.documentElement.setAttribute("data-bs-theme",d?"dark":"light")}catch(e){}})()`;
