"use client";

import { useState } from "react";
import { applyTheme, storedTheme, type ThemeChoice } from "@/lib/theme";

const CHOICES: [ThemeChoice, string][] = [["light", "Light"], ["dark", "Dark"], ["auto", "System"]];

export function ThemeSwitch() {
  // Only mounted once the Account menu is open, which is after hydration, so reading localStorage here is safe.
  const [choice, setChoice] = useState<ThemeChoice>(storedTheme);
  return (
    <div className="theme-switch" role="group" aria-labelledby="theme-label">
      {CHOICES.map(([value, label]) => (
        <button key={value} type="button" aria-pressed={choice === value}
          onClick={() => { applyTheme(value); setChoice(value); }}>{label}</button>
      ))}
    </div>
  );
}
