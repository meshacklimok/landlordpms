"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { NotificationTypeDetail } from "@/lib/types";

const channelName: Record<string, string> = {
  SMS: "SMS",
  EMAIL: "Email",
  WHATSAPP: "WhatsApp",
  PUSH: "Phone alert",
  INAPP: "In the app",
};

/** One message type: on or off, the channels it may use, when it goes out, and whether a landlord can turn it off. */
export function NotificationTypeForm({ detail }: { detail: NotificationTypeDetail }) {
  const router = useRouter();
  const url = `/console/web-api/notifications/${encodeURIComponent(detail.type.codename)}/`;
  const [enabled, setEnabled] = useState(detail.type.enabled);
  const [mandatory, setMandatory] = useState(detail.type.mandatory);
  const [urgent, setUrgent] = useState(detail.type.urgent);
  const [channels, setChannels] = useState<string[]>(detail.type.channels);
  const [offsets, setOffsets] = useState(detail.type.offsets.join(", "));
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});

  const toggle = (c: string) => setChannels((cur) => (cur.includes(c) ? cur.filter((x) => x !== c) : [...cur, c]));

  async function submit(payload: unknown, after?: () => void) {
    setBusy(true);
    setNotice(null);
    const result = await djangoPostJson<{ message: string }>(url, payload);
    setBusy(false);
    if (!result.ok) {
      setErrors(result.fields);
      const fieldErrors = Object.keys(result.fields).length > 0;
      return setNotice({ ok: false, text: fieldErrors ? "Check the fields marked below." : result.message });
    }
    setErrors({});
    setNotice({ ok: true, text: result.data.message });
    after?.();
  }

  function save(e: React.FormEvent) {
    e.preventDefault();
    const list = offsets.split(",").map((s) => s.trim()).filter(Boolean);
    if (list.some((s) => !/^-?\d+$/.test(s))) {
      setErrors({ offsets: ["Use whole numbers of days, separated by commas."] });
      return setNotice({ ok: false, text: "Check the fields marked below." });
    }
    void submit({ enabled, channels, offsets: list.map(Number), mandatory, urgent }, () => router.refresh());
  }

  const err = (field: string) =>
    errors[field]?.map((m) => (
      <p key={m} className="field-error" id={`${field}-err`}>
        {m}
      </p>
    ));

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href="/console/control/">Control</a> / <Link href="/console/control/notifications/">Notifications</Link>
          </p>
          <h1>{detail.type.label}</h1>
          <p className="lead-text">
            For {detail.type.audience}. <span className="muted small">{detail.type.codename}</span>
          </p>
        </div>
      </div>

      <div aria-live="polite">
        {notice && (
          <p className={notice.ok ? "notice-ok" : "notice-bad"} role={notice.ok ? "status" : "alert"}>
            {notice.text}
          </p>
        )}
      </div>

      <form className="card" onSubmit={save} noValidate>
        <div className="field">
          <span className="check">
            <input id="enabled" type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
            <label htmlFor="enabled">Send this message</label>
          </span>
          <span className="hint">Off stops it for everyone, unless it is marked required.</span>
        </div>

        <fieldset className="field" aria-describedby={errors.channels ? "channels-err" : undefined}>
          <legend>Sent by</legend>
          <div className="checks">
            {detail.available_channels.map((c) => (
              <span className="check" key={c}>
                <input id={`ch-${c}`} type="checkbox" checked={channels.includes(c)} onChange={() => toggle(c)} />
                <label htmlFor={`ch-${c}`}>{channelName[c] ?? c}</label>
              </span>
            ))}
          </div>
          {err("channels")}
        </fieldset>

        <div className="field">
          <label htmlFor="offsets">Days from the due date</label>
          <input
            id="offsets"
            type="text"
            inputMode="numeric"
            value={offsets}
            onChange={(e) => setOffsets(e.target.value)}
            aria-invalid={errors.offsets ? true : undefined}
            aria-describedby={errors.offsets ? "offsets-err" : "offsets-hint"}
          />
          <span className="hint" id="offsets-hint">
            For example -3, 0, 5: three days before, on the day, five days after. Leave empty if it is not tied to a date.
          </span>
          {err("offsets")}
        </div>

        <div className="field">
          <span className="check">
            <input id="mandatory" type="checkbox" checked={mandatory} onChange={(e) => setMandatory(e.target.checked)} />
            <label htmlFor="mandatory">Required</label>
          </span>
          <span className="hint">A landlord cannot switch it off.</span>
        </div>
        <div className="field">
          <span className="check">
            <input id="urgent" type="checkbox" checked={urgent} onChange={(e) => setUrgent(e.target.checked)} />
            <label htmlFor="urgent">Urgent</label>
          </span>
          <span className="hint">Goes out at any hour, not only in the daytime.</span>
        </div>

        {detail.placeholders.length > 0 && (
          <p className="muted small">
            The text can use:{" "}
            {detail.placeholders.map((p) => (
              <code key={p}>{`{${p}} `}</code>
            ))}
          </p>
        )}

        <div className="form-actions">
          <button className="btn btn-primary" type="submit" disabled={busy}>
            {busy ? "Saving…" : "Save"}
          </button>
          {detail.type.changed && (
            <button
              className="btn btn-outline"
              type="button"
              disabled={busy}
              onClick={() => {
                if (confirm("Go back to the built-in defaults for this message?")) {
                  void submit({ reset: true }, () => router.push("/console/control/notifications/"));
                }
              }}
            >
              Back to defaults
            </button>
          )}
          <Link className="btn btn-quiet" href="/console/control/notifications/">
            Cancel
          </Link>
        </div>
      </form>
    </>
  );
}
