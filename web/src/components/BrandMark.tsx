/** The NyumbaPMS mark: a roof over a solid N on a rounded tile. Colours follow the theme, so it matches the light and dark logo files. */
export function BrandMark({ className = "brand-mark" }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 64 64" aria-hidden="true" focusable="false">
      <rect width="64" height="64" rx="15" fill="var(--primary)" />
      <path
        fill="var(--on-primary)"
        stroke="var(--on-primary)"
        strokeWidth={2.4}
        strokeLinejoin="round"
        d="M17 21.75 L32 10.5 L47 21.75 V31.75 L32 20.5 L17 31.75 Z M17 53.5 V35.75 L25 29.75 L39 42.25 V29.75 L47 35.75 V53.5 H39 L25 41 V53.5 Z"
      />
    </svg>
  );
}
