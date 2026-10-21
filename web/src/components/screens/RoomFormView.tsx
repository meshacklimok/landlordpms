"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { djangoPostJson } from "@/lib/csrf";
import type { HostelFormData } from "@/lib/types";
import { HostelFields } from "./HostelFields";

type Answer = { message: string; done: string; redirect: string };

/** Adding a run of rooms, or changing one room (and archiving it). The Django form decides what is valid. */
export function RoomFormView({ initial, title, lead, submit, canArchive }: {
  initial: HostelFormData;
  title: string;
  lead: string;
  submit: string;
  canArchive?: boolean;
}) {
  const router = useRouter();
  const [values, setValues] = useState<Record<string, string>>(() =>
    Object.fromEntries(initial.fields.map((f) => [f.name, f.value])),
  );
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  async function send(payload: unknown) {
    setBusy(true);
    setNotice(null);
    setErrors({});
    const result = await djangoPostJson<Answer>(initial.post_url, payload);
    setBusy(false);
    if (!result.ok) {
      setErrors(result.fields);
      setNotice(result.message);
      return;
    }
    router.push(`${result.data.redirect}?done=${encodeURIComponent(result.data.done)}`);
  }

  return (
    <>
      <div className="page-head">
        <div>
          <p className="crumbs">
            <a href={initial.rooms_url}>Room map</a> / {initial.property.name}
          </p>
          <h1>{title}</h1>
          <p className="lead-text">{lead}</p>
        </div>
      </div>
      <div aria-live="polite">{notice && <p className="notice-bad" role="alert">{notice}</p>}</div>
      <section className="card">
        <form
          noValidate
          onSubmit={(e) => {
            e.preventDefault();
            void send({ values });
          }}
        >
          <HostelFields
            fields={initial.fields}
            values={values}
            errors={errors}
            onChange={(name, value) => setValues((cur) => ({ ...cur, [name]: value }))}
          />
          <div className="form-actions">
            <button className="btn btn-primary" type="submit" disabled={busy}>
              {busy ? "Saving…" : submit}
            </button>
            <a className="btn btn-outline" href={initial.rooms_url}>
              Cancel
            </a>
            {canArchive && (
              <button className="btn btn-outline" type="button" disabled={busy} onClick={() => void send({ action: "archive" })}>
                Archive this room
              </button>
            )}
          </div>
        </form>
      </section>
    </>
  );
}
