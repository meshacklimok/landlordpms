"use client";

import { usePathname } from "next/navigation";
import { djangoPost } from "@/lib/csrf";

export function TermsNotice({ acceptUrl, termsUrl, privacyUrl }: { acceptUrl: string; termsUrl: string; privacyUrl: string }) {
  const path = usePathname();
  async function accept() {
    const body = new FormData();
    body.set("next", path);
    await djangoPost(acceptUrl, body);
    window.location.reload();
  }
  return (
    <div className="alert alert-info app-notice">
      <i className="bi bi-file-earmark-text" aria-hidden="true" />
      <span>
        We have updated our Terms of Service and Privacy Policy. <a className="alert-link" href={termsUrl}>Read the Terms</a>
        {" · "}<a className="alert-link" href={privacyUrl}>Privacy Policy</a>
      </span>
      <button type="button" className="btn btn-primary btn-sm" onClick={() => void accept()}>I accept</button>
    </div>
  );
}
