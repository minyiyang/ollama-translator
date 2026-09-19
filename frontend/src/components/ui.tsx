import type { ReactNode } from "react";

export const Chip = ({ kind = "", children }: { kind?: string; children: ReactNode }) => (
  <span className={`chip ${kind}`}>{children}</span>
);

export const Bar = ({ percent, running = false }: { percent: number; running?: boolean }) => (
  <div className={`bar ${running ? "run" : ""}`}>
    <i style={{ width: `${Math.max(0, Math.min(100, percent))}%` }} />
  </div>
);

export const Card = ({ title, children, className = "" }: { title?: ReactNode; children: ReactNode; className?: string }) => (
  <section className={`card ${className}`}>
    {title && <h2>{title}</h2>}
    {children}
  </section>
);

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (value: boolean) => void; label: string }) {
  return (
    <label className="switch" title={label}>
      <input type="checkbox" checked={checked} aria-label={label} onChange={(e) => onChange(e.target.checked)} />
      <span />
    </label>
  );
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T;
  options: readonly (readonly [T, string])[];
  onChange: (value: T) => void;
}) {
  return (
    <div className="segmented" role="radiogroup">
      {options.map(([key, label]) => (
        <button key={key} type="button" role="radio" aria-checked={value === key} className={value === key ? "on" : ""} onClick={() => onChange(key)}>
          {label}
        </button>
      ))}
    </div>
  );
}

/** Highlight the first case-insensitive occurrence of `quote` in `text`. */
export function Highlight({ text, quote }: { text: string; quote?: string }) {
  if (!quote) return <>{text}</>;
  const at = text.toLowerCase().indexOf(quote.toLowerCase());
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark>{text.slice(at, at + quote.length)}</mark>
      {text.slice(at + quote.length)}
    </>
  );
}
