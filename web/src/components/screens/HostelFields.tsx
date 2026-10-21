"use client";

import type { HostelField } from "@/lib/types";

/** The fields a Django form describes (properties.web_api._describe), drawn one under another with their errors. */
export function HostelFields({
  fields,
  values,
  errors,
  onChange,
}: {
  fields: HostelField[];
  values: Record<string, string>;
  errors: Record<string, string[]>;
  onChange: (name: string, value: string) => void;
}) {
  return (
    <>
      {fields.map((f) => {
        const err = errors[f.name];
        const described = [f.help ? `${f.name}-hint` : "", err ? `${f.name}-err` : ""].filter(Boolean).join(" ") || undefined;
        const common = {
          id: f.name,
          name: f.name,
          required: f.required,
          "aria-invalid": err ? true : undefined,
          "aria-describedby": described,
        } as const;
        return (
          <div className="field" key={f.name}>
            {f.kind === "radio" ? (
              <fieldset aria-describedby={described}>
                <legend>{f.label}</legend>
                {f.choices?.map(([value, label]) => (
                  <label key={value} style={{ display: "block", marginBottom: ".35rem" }}>
                    <input
                      type="radio"
                      name={f.name}
                      value={value}
                      checked={values[f.name] === value}
                      onChange={() => onChange(f.name, value)}
                    />{" "}
                    {label}
                  </label>
                ))}
              </fieldset>
            ) : (
              <>
                <label htmlFor={f.name}>{f.label}</label>
                {f.kind === "select" ? (
                  <select {...common} value={values[f.name] ?? ""} onChange={(e) => onChange(f.name, e.target.value)}>
                    {f.choices?.map(([value, label]) => (
                      <option key={value} value={value}>
                        {label}
                      </option>
                    ))}
                  </select>
                ) : (
                  <input
                    {...common}
                    type={f.kind === "number" ? "number" : f.kind === "date" ? "date" : "text"}
                    inputMode={f.kind === "number" ? "decimal" : undefined}
                    step={f.kind === "number" ? "any" : undefined}
                    min={f.min}
                    max={f.max}
                    value={values[f.name] ?? ""}
                    onChange={(e) => onChange(f.name, e.target.value)}
                  />
                )}
              </>
            )}
            {f.help && (
              <span className="hint" id={`${f.name}-hint`}>
                {f.help}
              </span>
            )}
            {err?.map((m) => (
              <p key={m} className="field-error" id={`${f.name}-err`}>
                {m}
              </p>
            ))}
          </div>
        );
      })}
    </>
  );
}
