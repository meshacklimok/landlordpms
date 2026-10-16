"use client";

import { usePathname } from "next/navigation";

/** The Help button at the bottom left of every page: opens the contact form, remembering the page it was pressed on. */
export function HelpButton({ url }: { url: string }) {
  const path = usePathname();
  return (
    <a className="help-fab" href={`${url}?from=${encodeURIComponent(path)}`} aria-label="Get help">
      <i className="bi bi-chat-dots" aria-hidden="true" /><span>Help</span>
    </a>
  );
}
