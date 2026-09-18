import { useRef, useState } from "react";

export type SyntaxError_ = { message: string; line: number | null; column: number | null; context_line: number | null };

const LINE_HEIGHT = 20; // px; must match .code-editor in pages.css
const PAD_TOP = 8;

/**
 * Plain-text YAML editor with line numbers. Only syntax errors are shown here
 * (with a line marker); whether the values are valid is the Validate step's job.
 */
export function CodeEditor({
  value,
  onChange,
  readOnly,
  error,
}: {
  value: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error: SyntaxError_ | null;
}) {
  const area = useRef<HTMLTextAreaElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const lines = Math.max(1, value.split("\n").length);

  const goTo = (line: number) => {
    const el = area.current;
    if (!el) return;
    const all = el.value.split("\n");
    const start = all.slice(0, line - 1).reduce((n, text) => n + text.length + 1, 0);
    el.focus();
    el.setSelectionRange(start, start + (all[line - 1]?.length ?? 0));
    el.scrollTop = Math.max(0, (line - 4) * LINE_HEIGHT);
  };

  const band = (line: number | null, kind: string) =>
    line ? (
      <div className={`code-band ${kind}`} style={{ top: PAD_TOP + (line - 1) * LINE_HEIGHT - scrollTop, height: LINE_HEIGHT }} />
    ) : null;

  return (
    <div>
      <div className={`code-editor ${error ? "has-error" : ""}`}>
        <div className="code-gutter" aria-hidden="true">
          <div style={{ transform: `translateY(${-scrollTop}px)` }}>
            {Array.from({ length: lines }, (_, i) => {
              const n = i + 1;
              const kind = n === error?.line ? "err" : n === error?.context_line ? "ctx" : "";
              return <div key={n} className={kind}>{kind === "err" ? "▶ " : ""}{n}</div>;
            })}
          </div>
        </div>
        <div className="code-body">
          {band(error?.context_line ?? null, "ctx")}
          {band(error?.line ?? null, "err")}
          <textarea
            ref={area}
            className="code-text"
            spellCheck={false}
            wrap="off"
            value={value}
            readOnly={readOnly}
            aria-invalid={!!error}
            onScroll={(e) => setScrollTop(e.currentTarget.scrollTop)}
            onChange={(e) => onChange(e.target.value)}
          />
        </div>
      </div>
      {error ? (
        <div className="code-error" role="alert">
          <b>Syntax error{error.line ? ` on line ${error.line}${error.column ? `, column ${error.column}` : ""}` : ""}:</b> {error.message}
          {error.context_line && <> (the construct starts on line {error.context_line})</>}
          {error.line && <button type="button" className="small" onClick={() => goTo(error.line!)}>Go to line {error.line}</button>}
        </div>
      ) : (
        <div className="meta" style={{ marginTop: 6 }}>
          YAML syntax is valid. {readOnly ? "" : "Values are checked when you validate."}
        </div>
      )}
    </div>
  );
}
