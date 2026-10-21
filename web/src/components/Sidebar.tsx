"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { BrandMark } from "@/components/BrandMark";
import { djangoPost } from "@/lib/csrf";
import type { Session } from "@/lib/types";

type Props = { session: Session; open: boolean; onClose: () => void };

/** The menu by capability. Django owns most pages still, so these are plain links (a full page load), not client navigation. */
export function Sidebar({ session, open, onClose }: Props) {
  const path = usePathname();
  // The link whose address is the longest start of this path is the current one, as in the Django menu.
  const current = session.nav
    .flatMap((g) => g.items)
    .filter((i) => path.startsWith(i.url))
    .sort((a, b) => b.url.length - a.url.length)[0]?.url;

  async function switchTo(id: string) {
    const body = new FormData();
    body.set("organization", id);
    await djangoPost(session.urls.switch_org, body);
    window.location.assign("/"); // eslint-disable-line @next/next/no-location-assign-relative-destination
  }

  return (
    <aside id="side-menu" className={`sidebar${open ? " is-open" : ""}`} aria-label="Main menu">
      <div className="sidebar-head">
        <Link className="brand" href="/" onClick={onClose}>
          <BrandMark />
          <span>Nyumba<span className="brand-pms">PMS</span></span>
        </Link>
        <button type="button" className="icon-btn sidebar-close" onClick={onClose} aria-label="Close the menu">
          <i className="bi bi-x-lg" aria-hidden="true" />
        </button>
      </div>
      <nav className="sidebar-body">
        {session.memberships.length > 1 && (
          <div className="org-switch">
            <label className="side-heading" htmlFor="org-switch">Organization</label>
            <select id="org-switch" className="select-sm" value={session.memberships.find((m) => m.current)?.id}
              onChange={(e) => void switchTo(e.target.value)}>
              {session.memberships.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </div>
        )}
        <ul className="side-list">
          <li>
            <Link className={`side-link${path === "/" ? " active" : ""}`} href="/" aria-current={path === "/" ? "page" : undefined}>
              <i className="bi bi-grid-1x2" aria-hidden="true" />Home
            </Link>
          </li>
        </ul>
        {session.nav.map((group, n) => (
          <div key={group.label}>
            <p className="side-heading" id={`side-${n}`}>{group.label}</p>
            <ul className="side-list" aria-labelledby={`side-${n}`}>
              {group.items.map((item) => (
                <li key={item.url}>
                  <a href={item.url} className={`side-link${item.url === current ? " active" : ""}`}
                    aria-current={item.url === current ? "page" : undefined}>
                    <i className={`bi bi-${item.icon}`} aria-hidden="true" />
                    <span>{item.label}</span>
                    {item.badge > 0 && <span className="badge-count" title="Waiting for you">{item.badge}</span>}
                  </a>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </nav>
    </aside>
  );
}
