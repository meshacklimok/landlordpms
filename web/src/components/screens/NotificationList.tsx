import { djangoGet } from "@/lib/django";
import type { NotificationTypes } from "@/lib/types";

const channelName: Record<string, string> = {
  SMS: "SMS",
  EMAIL: "Email",
  WHATSAPP: "WhatsApp",
  PUSH: "Phone alert",
  INAPP: "In the app",
};

/** Every message the platform sends, with what is switched on, for the platform owner. */
export async function NotificationList() {
  const { types } = await djangoGet<NotificationTypes>("/console/web-api/notifications/");
  const changed = types.filter((t) => t.changed).length;
  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href="/console/control/">Control</a> / Notifications
          </p>
          <h1>Notification settings</h1>
          <p className="lead-text">
            {types.length} kinds of message. {changed} changed from the built-in defaults.
          </p>
        </div>
      </div>
      <div className="card table-wrap">
        <table className="data">
          <thead>
            <tr>
              <th scope="col">Message</th>
              <th scope="col">For</th>
              <th scope="col">Sent by</th>
              <th scope="col">State</th>
              <th scope="col">
                <span className="sr-only">Edit</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {types.map((t) => (
              <tr key={t.codename}>
                <th scope="row">
                  {t.label}
                  <br />
                  <span className="muted small">{t.codename}</span>
                </th>
                <td>{t.audience}</td>
                <td>{t.channels.map((c) => channelName[c] ?? c).join(", ") || <span className="muted">None</span>}</td>
                <td>
                  <span className={`pill${t.enabled ? "" : " pill-muted"}`}>{t.enabled ? "On" : "Off"}</span>{" "}
                  {t.mandatory && <span className="pill pill-warn">Required</span>}{" "}
                  {t.urgent && <span className="pill pill-warn">Urgent</span>}{" "}
                  {t.changed && <span className="pill pill-muted">Changed</span>}
                </td>
                <td>
                  <a className="btn btn-outline" href={`/console/control/notifications/${t.codename}/`}>
                    Edit
                  </a>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
