import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import {
  COMMON_GROUPS,
  GLOSSARY_REVIEW_FLAGS,
  HELP,
  LABELS,
  MODEL_PATHS,
  glossaryReviewMode,
  humanize,
  type GlossaryReviewMode,
  type SchemaField,
  type SchemaSection,
} from "../lib/configCatalog";
import { getPath, hasPath, sameValue, setPath, unsetPath, type Values } from "../lib/configValues";
import { CodeEditor, type SyntaxError_ } from "./CodeEditor";
import { FieldControl } from "./FieldControl";
import { Segmented, Switch } from "./ui";

type FieldError = { path: string; message: string };
type Tab = "options" | "all" | "yaml";

let schemaCache: Promise<SchemaSection[]> | null = null;
const loadSchema = () =>
  (schemaCache ??= api<{ sections: SchemaSection[] }>("/api/config/schema").then((s) => s.sections));

/**
 * Edits one YAML config through a typed form or as raw text. The YAML text is
 * the single value the parent owns; the form parses it and writes it back.
 */
export function ConfigEditor({
  text,
  onTextChange,
  hasComments,
  readOnly = false,
}: {
  text: string;
  onTextChange: (text: string) => void;
  hasComments: boolean;
  readOnly?: boolean;
}) {
  const [sections, setSections] = useState<SchemaSection[]>([]);
  const [values, setValues] = useState<Values>({});
  const [syntaxError, setSyntaxError] = useState<SyntaxError_ | null>(null);
  const [parseFailure, setParseFailure] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [installed, setInstalled] = useState<string[] | null>(null);
  const [tab, setTab] = useState<Tab>("options");
  const [query, setQuery] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const written = useRef<string | null>(null);
  const dumpSeq = useRef(0);

  useEffect(() => { loadSchema().then(setSections); }, []);

  const fields = useMemo(() => new Map(sections.flatMap((s) => s.fields.map((f) => [f.path, f] as const))), [sections]);

  // Text -> values, unless this text is what the form just wrote.
  useEffect(() => {
    if (text === written.current) return;
    const handle = window.setTimeout(() => {
      api<{ values: Values | null; syntax_error: SyntaxError_ | null }>("/api/config/parse", { text })
        .then((r) => {
          setParseFailure("");
          setSyntaxError(r.syntax_error);
          if (r.values) setValues(r.values);
        })
        // A request failure (server restarted, offline) is not a YAML problem; say so.
        .catch((e: Error) => setParseFailure(e.message));
    }, 250);
    return () => window.clearTimeout(handle);
  }, [text]);

  // Schema validation of whatever the text currently is.
  useEffect(() => {
    const handle = window.setTimeout(() => {
      api<{ errors: FieldError[] }>("/api/config/check", { text }).then((r) => setErrors(r.errors)).catch(() => {});
    }, 400);
    return () => window.clearTimeout(handle);
  }, [text]);

  const effective = (path: string) => (hasPath(values, path) ? getPath(values, path) : fields.get(path)?.default);
  const host = effective("ollama.host") ?? "http://localhost:11434";
  useEffect(() => {
    const handle = window.setTimeout(() => {
      api<{ installed: string[] | null }>(`/api/models?host=${encodeURIComponent(host)}`).then((r) => setInstalled(r.installed)).catch(() => setInstalled(null));
    }, 500);
    return () => window.clearTimeout(handle);
  }, [host]);

  const commit = (next: Values) => {
    setValues(next);
    const seq = ++dumpSeq.current;
    api<{ text: string }>("/api/config/dump", { values: next }).then((r) => {
      if (seq !== dumpSeq.current) return;
      written.current = r.text;
      onTextChange(r.text);
    });
  };
  const change = (path: string, value: unknown) => commit(setPath(values, path, value));
  const reset = (path: string) => commit(unsetPath(values, path));

  const errorFor = (path: string) => errors.find((e) => e.path === path)?.message;
  const knownPaths = new Set(fields.keys());
  const generalErrors = errors.filter((e) => !knownPaths.has(e.path));

  // Plain render functions (not nested components) so inputs keep focus across renders.
  const renderRow = (field: SchemaField) => {
    const value = effective(field.path);
    const modified = hasPath(values, field.path) && !sameValue(getPath(values, field.path), field.default);
    const error = errorFor(field.path);
    return (
      <div key={field.path} className={`opt ${modified ? "modified" : ""} ${error ? "invalid" : ""}`} id={`opt-${field.path}`}>
        <div>
          <div className="name">{LABELS[field.path] ?? humanize(field.key)}</div>
          <div className="path">{field.path}</div>
          {HELP[field.path] && <div className="help">{HELP[field.path]}</div>}
        </div>
        <div className="control">
          <FieldControl field={field} value={value} onChange={(v) => change(field.path, v)} installed={installed} isModel={MODEL_PATHS.has(field.path)} />
          {modified && (
            <button type="button" className="small" title={`Default: ${JSON.stringify(field.default)}`} onClick={() => reset(field.path)}>
              Reset
            </button>
          )}
        </div>
        {error && <div className="error">{error}</div>}
      </div>
    );
  };

  const renderGlossaryReview = () => {
    const mode = glossaryReviewMode(!!effective("workflow.require_glossary_review"), !!effective("workflow.llm_glossary_review"));
    const set = (next: GlossaryReviewMode) => {
      const flags = GLOSSARY_REVIEW_FLAGS[next];
      commit(setPath(setPath(values, "workflow.require_glossary_review", flags.require), "workflow.llm_glossary_review", flags.llm));
    };
    return (
      <div className="opt" key="glossary-review">
        <div>
          <div className="name">Glossary approval</div>
          <div className="path">workflow.require_glossary_review, workflow.llm_glossary_review</div>
          <div className="help">
            {mode === "llm" && "The LLM reviewer approves the glossary and the run continues."}
            {mode === "human" && "The run pauses so you can review the glossary on the Glossary page."}
            {mode === "none" && "The draft glossary is used unreviewed. Only for already-reviewed glossaries."}
          </div>
        </div>
        <div className="control">
          <Segmented value={mode} onChange={set} options={[["llm", "LLM reviews"], ["human", "I review"], ["none", "No review"]] as const} />
        </div>
      </div>
    );
  };

  const toggle = (key: string) =>
    setCollapsed((old) => { const next = new Set(old); if (!next.delete(key)) next.add(key); return next; });
  const setAll = (keys: string[], closed: boolean) =>
    setCollapsed((old) => { const next = new Set(old); keys.forEach((k) => (closed ? next.add(k) : next.delete(k))); return next; });

  /** Collapsible section; the header keeps counts visible while it is closed. */
  const renderGroup = (key: string, title: ReactNode, paths: string[], body: ReactNode, help?: string, id?: string) => {
    const closed = collapsed.has(key);
    const modified = paths.filter((p) => hasPath(values, p) && !sameValue(getPath(values, p), fields.get(p)?.default)).length;
    const invalid = paths.filter((p) => errorFor(p)).length;
    return (
      <div className={`opt-group ${closed ? "closed" : ""}`} key={key} id={id}>
        <button type="button" className="group-toggle" aria-expanded={!closed} onClick={() => toggle(key)}>
          <span className="caret">▾</span>
          <span className="group-title">{title}</span>
          {invalid > 0 && <span className="chip bad">{invalid} invalid</span>}
          {modified > 0 && <span className="chip running">{modified} changed</span>}
          <span className="meta">{paths.length} settings</span>
        </button>
        {!closed && (
          <div className="group-body">
            {help && <p className="meta">{help}</p>}
            {body}
          </div>
        )}
      </div>
    );
  };
  const optionPaths = (group: (typeof COMMON_GROUPS)[number]) =>
    group.options.flatMap((o) => ("special" in o ? ["workflow.require_glossary_review", "workflow.llm_glossary_review"] : [o.path]));

  const q = query.trim().toLowerCase();
  const matches = (f: SchemaField) =>
    (!q || f.path.toLowerCase().includes(q) || (LABELS[f.path] ?? "").toLowerCase().includes(q)) &&
    (!changedOnly || (hasPath(values, f.path) && !sameValue(getPath(values, f.path), f.default)));

  return (
    <div>
      <datalist id="installed-models">{(installed ?? []).map((m) => <option key={m} value={m} />)}</datalist>
      <div className="tabs-inline" role="tablist">
        {([["options", "Options"], ["all", "All settings"], ["yaml", "YAML"]] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      {parseFailure && (
        <div className="banner bad">Could not check the configuration with the server: {parseFailure}</div>
      )}
      {syntaxError && tab !== "yaml" && (
        <div className="banner bad">
          The YAML has a syntax error{syntaxError.line ? ` on line ${syntaxError.line}` : ""}, so the form shows its last valid state.{" "}
          <button type="button" className="small" onClick={() => setTab("yaml")}>Open the YAML tab</button>
        </div>
      )}
      {!syntaxError && tab !== "yaml" && generalErrors.length > 0 && (
        <div className="banner bad">
          {generalErrors.map((e, i) => <div key={i}>{e.path ? <b className="mono">{e.path}: </b> : null}{e.message}</div>)}
        </div>
      )}
      {hasComments && !readOnly && tab !== "yaml" && (
        <p className="meta">Changing an option rewrites the YAML from the form; comments in the file are not kept.</p>
      )}

      {tab === "options" && sections.length > 0 && (
        <>
          <div className="row group-actions">
            <button type="button" className="small" onClick={() => setAll(COMMON_GROUPS.map((g) => `opt:${g.title}`), false)}>Expand all</button>
            <button type="button" className="small" onClick={() => setAll(COMMON_GROUPS.map((g) => `opt:${g.title}`), true)}>Collapse all</button>
          </div>
          <fieldset disabled={readOnly} className="plain-fieldset">
            {COMMON_GROUPS.map((group) =>
              renderGroup(
                `opt:${group.title}`,
                group.title,
                optionPaths(group),
                group.options.map((option) =>
                  "special" in option ? renderGlossaryReview() : fields.has(option.path) ? renderRow(fields.get(option.path)!) : null,
                ),
                group.help,
              ),
            )}
          </fieldset>
        </>
      )}

      {tab === "all" && (
        <>
          {/* Stays pinned under the page header while the settings scroll. */}
          <div className="settings-toolbar">
            <div className="row" style={{ marginTop: 0 }}>
              <input type="text" placeholder="Filter settings, e.g. num_ctx" value={query} onChange={(e) => setQuery(e.target.value)} style={{ maxWidth: 320 }} />
              <label className="row meta" style={{ margin: 0 }}>
                <Switch checked={changedOnly} onChange={setChangedOnly} label="Only changed from default" /> only changed from default
              </label>
              <span style={{ flex: 1 }} />
              <button type="button" className="small" onClick={() => setAll(sections.map((s) => `all:${s.key}`), false)}>Expand all</button>
              <button type="button" className="small" onClick={() => setAll(sections.map((s) => `all:${s.key}`), true)}>Collapse all</button>
            </div>
            <div className="section-nav">
              {sections.map((s) => (
                <button key={s.key} type="button" className="small" onClick={() => {
                  setAll([`all:${s.key}`], false);
                  window.setTimeout(() => document.getElementById(`sec-${s.key}`)?.scrollIntoView({ behavior: "smooth" }), 0);
                }}>
                  {s.title}
                </button>
              ))}
            </div>
          </div>
          <fieldset disabled={readOnly} className="plain-fieldset">
          {sections.map((section) => {
            const shown = section.fields.filter(matches);
            if (!shown.length) return null;
            return renderGroup(
              `all:${section.key}`,
              <>{section.title} <span className="meta mono">{section.key}</span></>,
              shown.map((f) => f.path),
              shown.map(renderRow),
              undefined,
              `sec-${section.key}`,
            );
          })}
          </fieldset>
        </>
      )}

      {tab === "yaml" && (
        <>
          <CodeEditor
            value={text}
            readOnly={readOnly}
            error={syntaxError}
            onChange={(next) => { written.current = null; onTextChange(next); }}
          />
        </>
      )}
    </div>
  );
}
