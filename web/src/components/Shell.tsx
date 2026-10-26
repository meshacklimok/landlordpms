import type { ReactNode } from "react";
import { djangoGet } from "@/lib/django";
import type { Session } from "@/lib/types";
import { AskButton } from "./AskButton";
import { HelpButton } from "./HelpButton";
import { InstallPrompt } from "./InstallPrompt";
import { Notices } from "./Notices";
import { ShellFrame } from "./ShellFrame";

/** The frame around every signed-in screen, as base.html draws it: menu, top bar, notices, Help button. */
export async function Shell({ children }: { children: ReactNode }) {
  const session = await djangoGet<Session>("/web-api/session/");
  return (
    <ShellFrame session={session}>
      <Notices session={session} />
      {children}
      <HelpButton url={session.urls.contact} />
      {session.show_ask && <AskButton />}
      <InstallPrompt />
    </ShellFrame>
  );
}
