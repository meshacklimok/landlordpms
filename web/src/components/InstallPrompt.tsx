"use client";

import { usePathname } from "next/navigation";
import { useEffect, useState, useSyncExternalStore } from "react";
import { BrandMark } from "./BrandMark";

type InstallEvent = Event & { prompt: () => Promise<void>; userChoice: Promise<{ outcome: string }> };

const DELAY_MS = 10_000;
const LATER_DAYS = 7;
const KEY = "install-prompt-later";

function laterActive(): boolean {
  try {
    const at = Number(localStorage.getItem(KEY));
    return at > 0 && Date.now() - at < LATER_DAYS * 86_400_000;
  } catch {
    return false;
  }
}

/** A bar at the bottom of the home page on a phone, 10 seconds in: install the app now or later (D-080). */
export function InstallPrompt() {
  const path = usePathname();
  const [shown, setShown] = useState(false);
  // Read from the browser without a state set inside an effect; the server (and the first paint) says "not an iPhone".
  const ios = useSyncExternalStore(
    () => () => {},
    () => /iphone|ipad|ipod/i.test(navigator.userAgent),
    () => false,
  );
  const [event, setEvent] = useState<InstallEvent | null>(null);

  useEffect(() => {
    const onPrompt = (e: Event) => {
      e.preventDefault();
      setEvent(e as InstallEvent);
    };
    const onInstalled = () => {
      setEvent(null);
      setShown(false);
    };
    window.addEventListener("beforeinstallprompt", onPrompt);
    window.addEventListener("appinstalled", onInstalled);
    return () => {
      window.removeEventListener("beforeinstallprompt", onPrompt);
      window.removeEventListener("appinstalled", onInstalled);
    };
  }, []);

  useEffect(() => {
    if (path !== "/") return;
    const phone = window.matchMedia("(max-width: 767.98px)").matches && window.matchMedia("(pointer: coarse)").matches;
    const installed =
      window.matchMedia("(display-mode: standalone)").matches ||
      (navigator as Navigator & { standalone?: boolean }).standalone === true;
    if (!phone || installed || laterActive()) return;
    const timer = window.setTimeout(() => setShown(true), DELAY_MS);
    return () => window.clearTimeout(timer);
  }, [path]);

  // Android shows the real install dialog; an iPhone has none, so it gets the two steps instead. Any other browser stays quiet.
  if (!shown || path !== "/" || (!event && !ios)) return null;

  const later = () => {
    try {
      localStorage.setItem(KEY, String(Date.now()));
    } catch {
      /* the bar just comes back next visit */
    }
    setShown(false);
  };

  const install = async () => {
    if (!event) return;
    await event.prompt();
    await event.userChoice.catch(() => undefined);
    setEvent(null);
    setShown(false);
  };

  return (
    <section className="install-bar" role="dialog" aria-label="Install the app">
      <BrandMark />
      <div className="install-bar-text">
        <strong>Install NyumbaPMS</strong>
        <span>
          {event ? "Open it from your home screen, like any app." : "Tap Share, then Add to Home Screen."}
        </span>
      </div>
      <div className="install-bar-actions">
        <button className="btn btn-quiet" type="button" onClick={later}>Later</button>
        {event && <button className="btn btn-primary" type="button" onClick={install}>Install</button>}
      </div>
    </section>
  );
}
