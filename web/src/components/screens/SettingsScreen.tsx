import { djangoGet } from "@/lib/django";
import type { OrganizationSettings } from "@/lib/types";
import { SettingsForm } from "./SettingsForm";

/** The organization's own details and billing habits, with links to every other settings page (D-123). */
export async function SettingsScreen() {
  const settings = await djangoGet<OrganizationSettings>("/organization/web-api/");
  return (
    <>
      <div className="page-head">
        <div>
          <h1>Settings</h1>
          <p className="lead-text">
            Your organization&apos;s details and how rent is billed. Changes apply to letters and invoices made from
            now on; ones already issued do not change.
          </p>
        </div>
      </div>
      <div className="settings-layout">
        <SettingsForm initial={settings} />
        {settings.pages.length > 0 && (
          <nav className="card settings-pages" aria-labelledby="other-settings">
            <h2 id="other-settings">Other settings</h2>
            <ul>
              {settings.pages.map((page) => (
                <li key={page.url}>
                  <a href={page.url}>
                    <strong>{page.label}</strong>
                    <span className="muted small">{page.hint}</span>
                  </a>
                </li>
              ))}
            </ul>
          </nav>
        )}
      </div>
    </>
  );
}
