import { Suspense } from "react";
import { Shell } from "@/components/Shell";

/** Screens for a member of an organization: the menu, who is signed in, sign out. */
export default function AppLayout({ children }: LayoutProps<"/">) {
  return (
    <Suspense fallback={<div className="shell-loading" role="status">Loading…</div>}>
      <Shell>{children}</Shell>
    </Suspense>
  );
}
