import type { Metadata } from "next";
import Link from "next/link";
import { BrandMark } from "@/components/BrandMark";

export const metadata: Metadata = { title: { default: "Console", template: "%s · Console · NyumbaPMS" } };

/** The platform owner's console. Its people need no organization, so it has no organization menu (Shell would send them away). */
export default function ConsoleLayout({ children }: LayoutProps<"/">) {
  return (
    <>
      <header className="console-bar">
        <a href="/console/" className="console-brand"><BrandMark /><span>Nyumba<span className="brand-pms">PMS</span></span> <span className="muted">Console</span></a>
        <Link href="/console/control/notifications/" className="btn btn-quiet">Control</Link>
      </header>
      <main id="main" className="console-main" tabIndex={-1}>{children}</main>
    </>
  );
}
