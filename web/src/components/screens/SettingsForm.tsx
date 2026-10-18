"use client";

import { useEffect, useRef, useState } from "react";
import { djangoPostForm } from "@/lib/csrf";
import type { FormField, OrganizationSettings } from "@/lib/types";

type Value = string | boolean | string[] | null;

/** The picture just chosen, shown before it is saved so a wrong file is noticed early. */
function PickedPreview({ file, label }: { file: File; label: string }) {
  const [url] = useState(() => URL.createObjectURL(file));
  useEffect(() => () => URL.revokeObjectURL(url), [url]);
  // eslint-disable-next-line @next/next/no-img-element -- a local preview of the chosen file
  return <img className="settings-picture" src={url} alt={`New ${label.toLowerCase()}: ${file.name}`} />;
}

/** Which fields sit together. A field the server does not send (it does not apply to this kind of business) is skipped. */
const SECTIONS: { id: string; title: string; hint?: string; names: string[] }[] = [
  {
    id: "business",
    title: "Your business",
    names: ["name", "org_type", "brand_name", "billing_phone", "billing_email", "address", "document_footer"],
  },
  {
    id: "look",
    title: "Invoices and letters",
    hint: "What tenants see printed.",
    names: [
      "logo",
      "stamp",
      "brand_color",
      "invoice_payment_info",
      "invoice_terms",
      "invoice_show_tenant_contact",
      "invoice_show_previous_balance",
    ],
  },
  {
    id: "billing",
    title: "Billing",
    names: ["invoice_lead_days", "prorate_partial_months", "holds_deposits"],
  },
  {
    id: "late",
    title: "Late fees",
    names: ["late_fee_mode", "late_fee_amount", "late_fee_percent", "late_fee_cap"],
  },
  {
    id: "deposits",
    title: "Deposits",
    names: ["deposit_refund_days", "deposit_deduction_headings", "deposit_proof_required", "deposit_allow_offset"],
  },
];
/** The tick-box that goes with a picture is drawn beside it, not as a field of its own. */
const REMOVE_OF: Record<string, string> = { logo: "remove_logo", stamp: "remove_stamp" };
const REMOVERS = new Set(Object.values(REMOVE_OF));

function startValues(fields: FormField[]): Record<string, Value> {
  return Object.fromEntries(fields.map((f) => [f.name, f.value]));
}

export function SettingsForm({ initial }: { initial: OrganizationSettings }) {
  const [data, setData] = useState(initial);
  const [values, setValues] = useState(() => startValues(initial.fields));
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ ok: boolean; text: string } | null>(null);
  const [errors, setErrors] = useState<Record<string, string[]>>({});
  const summary = useRef<HTMLDivElement>(null);

  const byName = new Map(data.fields.map((f) => [f.name, f]));
  const set = (name: string, value: Value) => setValues((cur) => ({ ...cur, [name]: value }));
  const mode = values.late_fee_mode;

  // The late-fee boxes only matter for the kind of fee chosen.
  const hidden = (name: string) =>
    (name === "late_fee_amount" && mode !== "FIXED") ||
    (name === "late_fee_percent" && mode !== "PERCENT") ||
    (name === "late_fee_cap" && mode === "OFF");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNotice(null);
    const form = new FormData();
    for (const f of data.fields) {
      const v = values[f.name];
      if (f.kind === "image") {
        const file = files[f.name];
        if (file) form.append(f.name, file);
      } else if (f.kind === "checkbox") {
        if (v) form.append(f.name, "on");
      } else if (f.kind === "multicheck") {
        for (const item of (v as string[]) ?? []) form.append(f.name, item);
      } else {
        form.append(f.name, String(v ?? ""));
      }
    }
    const result = await djangoPostForm<OrganizationSettings & { message: string }>(data.post_url, form);
    setBusy(false);
    if (!result.ok) {
      setErrors(result.fields);
      setNotice({ ok: false, text: result.message });
      requestAnimationFrame(() => summary.current?.focus());
      return;
    }
    setErrors({});
    setFiles({});
    setData(result.data);
    setValues(startValues(result.data.fields));
    setNotice({ ok: true, text: result.data.message });
  }

  const errorList = (name: string) =>
    errors[name]?.map((m) => (
      <p key={m} className="field-error" id={`${name}-err`}>
        {m}
      </p>
    ));
  const describedBy = (f: FormField) =>
    [f.help ? `${f.name}-hint` : "", errors[f.name] ? `${f.name}-err` : ""].filter(Boolean).join(" ") || undefined;
  const hint = (f: FormField) =>
    f.help ? (
      <span className="hint" id={`${f.name}-hint`}>
        {f.help}
      </span>
    ) : null;

  function control(f: FormField) {
    const v = values[f.name];
    const common = {
      id: f.name,
      name: f.name,
      "aria-invalid": errors[f.name] ? true : undefined,
      "aria-describedby": describedBy(f),
    } as const;
    switch (f.kind) {
      case "textarea":
        return (
          <textarea
            {...common}
            rows={3}
            maxLength={f.max_length}
            value={String(v ?? "")}
            onChange={(e) => set(f.name, e.target.value)}
          />
        );
      case "select":
        return (
          <select {...common} value={String(v ?? "")} onChange={(e) => set(f.name, e.target.value)}>
            {f.choices?.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        );
      case "number":
        return (
          <input
            {...common}
            type="number"
            inputMode={f.inputmode === "numeric" ? "numeric" : "decimal"}
            min={f.min}
            max={f.max}
            step={f.inputmode === "numeric" ? 1 : "0.01"}
            placeholder={f.placeholder}
            value={String(v ?? "")}
            onChange={(e) => set(f.name, e.target.value)}
          />
        );
      default:
        return (
          <input
            {...common}
            type={f.kind === "email" ? "email" : "text"}
            inputMode={f.inputmode as "tel" | undefined}
            maxLength={f.max_length}
            placeholder={f.placeholder}
            required={f.required}
            value={String(v ?? "")}
            onChange={(e) => set(f.name, e.target.value)}
          />
        );
    }
  }

  function field(f: FormField) {
    if (hidden(f.name)) return null;
    if (f.kind === "checkbox") {
      return (
        <div className="field" key={f.name}>
          <span className="check">
            <input
              id={f.name}
              type="checkbox"
              checked={Boolean(values[f.name])}
              aria-describedby={describedBy(f)}
              onChange={(e) => set(f.name, e.target.checked)}
            />
            <label htmlFor={f.name}>{f.label}</label>
          </span>
          {hint(f)}
          {errorList(f.name)}
        </div>
      );
    }
    if (f.kind === "multicheck") {
      const chosen = (values[f.name] as string[]) ?? [];
      return (
        <fieldset className="field" key={f.name} aria-describedby={describedBy(f)}>
          <legend>{f.label}</legend>
          <div className="checks">
            {f.choices?.map(([value, label]) => (
              <span className="check" key={value}>
                <input
                  id={`${f.name}-${value}`}
                  type="checkbox"
                  checked={chosen.includes(value)}
                  onChange={() =>
                    set(f.name, chosen.includes(value) ? chosen.filter((x) => x !== value) : [...chosen, value])
                  }
                />
                <label htmlFor={`${f.name}-${value}`}>{label}</label>
              </span>
            ))}
          </div>
          {hint(f)}
          {errorList(f.name)}
        </fieldset>
      );
    }
    if (f.kind === "image") {
      const current = f.name === "logo" ? data.logo_url : data.stamp_url;
      const removeName = REMOVE_OF[f.name];
      const picked = files[f.name];
      return (
        <div className="field" key={f.name}>
          <label htmlFor={f.name}>{f.label}</label>
          {current && !picked && !values[removeName] && (
            // eslint-disable-next-line @next/next/no-img-element -- an uploaded picture from Django's media, not a build asset
            <img className="settings-picture" src={current} alt={`Current ${f.label.toLowerCase()}`} />
          )}
          {picked && <PickedPreview key={`${picked.name}-${picked.lastModified}`} file={picked} label={f.label} />}
          <input
            id={f.name}
            type="file"
            accept={f.accept}
            aria-describedby={describedBy(f)}
            aria-invalid={errors[f.name] ? true : undefined}
            onChange={(e) => setFiles((cur) => ({ ...cur, [f.name]: e.target.files?.[0] ?? null }))}
          />
          {hint(f)}
          {errorList(f.name)}
          {current && byName.has(removeName) && (
            <span className="check">
              <input
                id={removeName}
                type="checkbox"
                checked={Boolean(values[removeName])}
                onChange={(e) => set(removeName, e.target.checked)}
              />
              <label htmlFor={removeName}>{byName.get(removeName)?.label}</label>
            </span>
          )}
        </div>
      );
    }
    return (
      <div className="field" key={f.name}>
        <label htmlFor={f.name}>{f.label}</label>
        {control(f)}
        {hint(f)}
        {errorList(f.name)}
      </div>
    );
  }

  const placed = new Set(SECTIONS.flatMap((s) => s.names));
  const leftover = data.fields.filter((f) => !placed.has(f.name) && !REMOVERS.has(f.name)).map((f) => f.name);
  const sections = [...SECTIONS, ...(leftover.length ? [{ id: "more", title: "More", names: leftover }] : [])];
  const withErrors = Object.keys(errors).filter((k) => k !== "__all__");

  return (
    <form onSubmit={submit} noValidate className="settings-form">
      <div aria-live="polite">
        {notice?.ok && (
          <p className="notice-ok" role="status">
            {notice.text}
          </p>
        )}
      </div>
      {notice && !notice.ok && (
        <div className="notice-bad" role="alert" tabIndex={-1} ref={summary}>
          <strong>{notice.text}</strong>
          {withErrors.length > 0 && (
            <ul>
              {withErrors.map((name) => (
                <li key={name}>
                  <a href={`#${name}`}>{byName.get(name)?.label ?? name}</a>: {errors[name][0]}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {sections.map((section) => {
        const rows = section.names.map((n) => byName.get(n)).filter((f): f is FormField => Boolean(f));
        if (rows.length === 0) return null;
        return (
          <section className="card" key={section.id} aria-labelledby={`sec-${section.id}`}>
            <h2 id={`sec-${section.id}`}>{section.title}</h2>
            {section.hint && <p className="muted small">{section.hint}</p>}
            {rows.map(field)}
          </section>
        );
      })}

      <div className="form-actions">
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}
