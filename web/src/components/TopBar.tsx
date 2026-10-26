"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { BrandMark } from "@/components/BrandMark";
import { djangoPost } from "@/lib/csrf";
import type { Session } from "@/lib/types";
import { GlobalSearch } from "./GlobalSearch";
import { ThemeSwitch } from "./ThemeSwitch";

type Props = { session: Session; menuOpen: boolean; onMenu: () => void };

/** The bar across the top: menu button on a phone, search, notifications and the Account menu. */
export function TopBar({ session, menuOpen, onMenu }: Props) {
  const { urls, user, organization } = session;
  const [account, setAccount] = useState(false);
  const [signOutFailed, setSignOutFailed] = useState(false);
  const holder = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!account) return;
    const outside = (e: MouseEvent) => {
      if (!holder.current?.contains(e.target as Node)) setAccount(false);
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key === "Escape") setAccount(false);
    };
    document.addEventListener("mousedown", outside);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", outside);
      document.removeEventListener("keydown", esc);
    };
  }, [account]);

  async function signOut() {
    setSignOutFailed(false);
    try {
      const response = await djangoPost(session.logout_url);
      if (!response.ok) throw new Error(String(response.status));
    } catch {
      setSignOutFailed(true); // stay signed in and say so, rather than look signed out
      return;
    }
    // The front page is a Django page for signed-out visitors, so leave the app with a full page load.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign("/");
  }

  const unread = session.unread_count;
  return (
    <header className="topbar">
      <div className="topbar-inner">
        <button type="button" className="icon-btn menu-btn" aria-expanded={menuOpen} aria-controls="side-menu" onClick={onMenu}>
          <i className="bi bi-list" aria-hidden="true" />
          <span className="visually-hidden">Open the menu</span>
        </button>
        <Link className="brand brand-top" href="/">
          <BrandMark />
          <span>Nyumba<span className="brand-pms">PMS</span></span>
        </Link>
        <GlobalSearch action={urls.search} suggestUrl={urls.suggest} />
        <div className="topbar-end">
          <a
            className="icon-btn"
            href={urls.inbox}
            aria-label={unread ? `${unread} unread notification${unread === 1 ? "" : "s"}` : "Notifications"}
          >
            <i className="bi bi-bell" aria-hidden="true" />
            {unread > 0 && <span className="notif-badge" aria-hidden="true">{unread > 99 ? "99+" : unread}</span>}
          </a>
          <div className="dropdown" ref={holder}>
            <button type="button" className="account-btn" aria-expanded={account} aria-haspopup="menu" onClick={() => setAccount((v) => !v)}>
              <span className="avatar" aria-hidden="true">{user.initial}</span>
              <span className="label-md-up">Account</span>
              <i className="bi bi-chevron-down" aria-hidden="true" />
            </button>
            {account && (
              <ul className="account-menu" role="menu">
                <li className="account-menu-head">
                  <span className="avatar avatar-lg" aria-hidden="true">{user.initial}</span>
                  <span className="account-who">
                    <b className="trunc">{user.name}</b>
                    <span className="muted small trunc">{organization.name}</span>
                  </span>
                </li>
                <li><hr /></li>
                <li><a className="dropdown-item" role="menuitem" href={urls.help}><i className="bi bi-question-circle" aria-hidden="true" />Help</a></li>
                {!session.in_demo && (
                  <li><a className="dropdown-item" role="menuitem" href={urls.security}><i className="bi bi-shield-check" aria-hidden="true" />Security</a></li>
                )}
                {session.has_console && (
                  <li><a className="dropdown-item" role="menuitem" href={urls.console}><i className="bi bi-speedometer" aria-hidden="true" />Console</a></li>
                )}
                <li className="theme-row">
                  <div className="small muted" id="theme-label">Theme</div>
                  <ThemeSwitch />
                </li>
                <li><hr /></li>
                <li>
                  <button type="button" className="dropdown-item" role="menuitem" onClick={() => void signOut()}>
                    <i className="bi bi-box-arrow-right" aria-hidden="true" />Log out
                  </button>
                  {signOutFailed && <p className="bad small account-err" role="alert">Could not sign you out. Try again.</p>}
                </li>
              </ul>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}
