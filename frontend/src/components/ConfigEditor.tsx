import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useT, type MessageKey } from "../i18n";
import {
  COMMON_GROUPS,
  GLOSSARY_REVIEW_FLAGS,
  HELP,
  LABELS,
  MODEL_PATHS,
  SPECIAL_PATHS,
  glossaryReviewMode,
  humanize,
  type GlossaryReviewMode,
  type SchemaField,
  type SchemaSection,
} from "../lib/configCatalog";
import { getPath, hasPath, sameValue, setPath, unsetPath, type Values } from "../lib/configValues";
import { hiddenOptions, loadLanguages, pairOf, type LanguageSupport } from "../lib/languages";
import { CodeEditor, type SyntaxError_ } from "./CodeEditor";
import { FieldControl } from "./FieldControl";
import { LanguagePairPicker } from "./LanguagePairPicker";
import { Segmented, Switch } from "./ui";

type FieldError = { path: string; message: string };
type Tab = "options" | "all" | "yaml";

const GLOSSARY_REVIEW_HELP: Record<GlossaryReviewMode, MessageKey> = {
  llm: "config.glossaryReview.help.llm",
  human: "config.glossaryReview.help.human",
  none: "config.glossaryReview.help.none",
};

let schemaCache: Promise<SchemaSection[]> | null = null;
function loadSchema() {
  if (!schemaCache) {
    const request = api<{ sections: SchemaSection[] }>("/api/config/schema").then((s) => s.sections);
    // A failed load is not kept: the next editor asks again.
    request.catch(() => { if (schemaCache === request) schemaCache = null; });
    schemaCache = request;
  }
  return schemaCache;
}

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
  const t = useT();
  const [sections, setSections] = useState<SchemaSection[]>([]);
  const [values, setValues] = useState<Values>({});
  const [syntaxError, setSyntaxError] = useState<SyntaxError_ | null>(null);
  const [parseFailure, setParseFailure] = useState("");
  const [schemaFailure, setSchemaFailure] = useState("");
  const [writeFailure, setWriteFailure] = useState("");
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [errors, setErrors] = useState<FieldError[]>([]);
  const [installed, setInstalled] = useState<string[] | null>(null);
  const [tab, setTab] = useState<Tab>("options");
  const [query, setQuery] = useState("");
  const [changedOnly, setChangedOnly] = useState(false);
  const written = useRef<string | null>(null);
  const dumpSeq = useRef(0);
  // The values the YAML text holds: what the form goes back to when a change cannot be written.
  const confirmed = useRef<Values>({});
  // Whether the text is still being read into the form, and which reading is the latest.
  const [reading, setReading] = useState(true);
  const parseSeq = useRef(0);

  useEffect(() => { loadSchema().then(setSections).catch((e: Error) => setSchemaFailure(e.message)); }, []);

  const fields = useMemo(() => new Map(sections.flatMap((s) => s.fields.map((f) => [f.path, f] as const))), [sections]);

  // Text -> values, unless this text is what the form just wrote. Until the
  // text has been read the form shows defaults, not the file: a change made
  // then would be lost, or written back without the file's other settings. So
  // the form waits.
  useEffect(() => {
    if (text === written.current) return;
    setReading(true);
    const seq = ++parseSeq.current;
    const handle = window.setTimeout(() => {
      api<{ values: Values | null; syntax_error: SyntaxError_ | null }>("/api/config/parse", { text })
        .then((r) => {
          if (seq !== parseSeq.current) return;
          setParseFailure("");
          setSyntaxError(r.syntax_error);
          if (r.values) { confirmed.current = r.values; setValues(r.values); }
        })
        // A request failure (server restarted, offline) is not a YAML problem; say so.
        .catch((e: Error) => { if (seq === parseSeq.current) setParseFailure(e.message); })
        .finally(() => { if (seq === parseSeq.current) setReading(false); });
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

  // The pair is `translation.direction`, or the two language fields when the YAML sets them.
  const source = effective("translation.source_language");
  const target = effective("translation.target_language");
  const pair: string = source && target ? pairOf(String(source), String(target)) : String(effective("translation.direction") ?? "en-zh");
  const [support, setSupport] = useState<LanguageSupport | null>(null);
  useEffect(() => {
    let current = true;
    loadLanguages(pair).then((r) => current && setSupport(r.support)).catch(() => current && setSupport(null));
    return () => { current = false; };
  }, [pair]);
  const hidden = hiddenOptions(support);

  const commit = (next: Values) => {
    setValues(next);
    const seq = ++dumpSeq.current;
    api<{ text: string }>("/api/config/dump", { values: next })
      .then((r) => {
        if (seq !== dumpSeq.current) return;
        setWriteFailure("");
        confirmed.current = next;
        written.current = r.text;
        onTextChange(r.text);
      })
      // The YAML was not rewritten, so the form goes back to what the YAML says:
      // not to the change before this one, which may never have been written either.
      .catch((e: Error) => {
        if (seq !== dumpSeq.current) return;
        setValues(confirmed.current);
        setWriteFailure(e.message);
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
            <button type="button" className="small" title={t("config.editor.default", { value: String(JSON.stringify(field.default)) })} onClick={() => reset(field.path)}>
              {t("config.editor.reset")}
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
          <div className="name">{t("config.glossaryReview.title")}</div>
          <div className="path">workflow.require_glossary_review, workflow.llm_glossary_review</div>
          <div className="help">{t(GLOSSARY_REVIEW_HELP[mode])}</div>
        </div>
        <div className="control">
          <Segmented value={mode} onChange={set} options={[["llm", t("config.glossaryReview.llm")], ["human", t("config.glossaryReview.human")], ["none", t("config.glossaryReview.none")]] as const} />
        </div>
      </div>
    );
  };

  const renderLanguagePair = () => (
    <div className="opt" key="language-pair">
      <div>
        <div className="name">{t("config.languages.title")}</div>
        <div className="path">translation.direction</div>
        <div className="help">{t("config.languages.help")}</div>
      </div>
      <div className="control">
        <LanguagePairPicker
          value={pair}
          disabled={readOnly || reading}
          onChange={(next) =>
            commit(unsetPath(unsetPath(setPath(values, "translation.direction", next), "translation.source_language"), "translation.target_language"))
          }
        />
      </div>
      {errorFor("translation.direction") && <div className="error">{errorFor("translation.direction")}</div>}
    </div>
  );

  /** A line naming the options this pair does not use, and why. */
  const renderHidden = (group: (typeof COMMON_GROUPS)[number]) => {
    const names = group.options.flatMap((o) => ("path" in o && hidden.has(o.path) ? [[o.label, hidden.get(o.path)!.reason] as const] : []));
    if (!names.length) return null;
    return (
      <p className="meta" key="hidden">
        {t("config.editor.notUsed", { pair, options: names.map(([label, reason]) => `${label} (${reason})`).join("; ") })}
      </p>
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
          {invalid > 0 && <span className="chip bad">{t("config.editor.invalidCount", { count: invalid })}</span>}
          {modified > 0 && <span className="chip running">{t("config.editor.changedCount", { count: modified })}</span>}
          <span className="meta">{t("config.editor.settingCount", { count: paths.length })}</span>
        </button>
        {!closed && (
          <fieldset disabled={readOnly || reading} className="plain-fieldset group-body">
            {help && <p className="meta">{help}</p>}
            {body}
          </fieldset>
        )}
      </div>
    );
  };
  const optionPaths = (group: (typeof COMMON_GROUPS)[number]) =>
    group.options.flatMap((o) => ("special" in o ? SPECIAL_PATHS[o.special] : hidden.has(o.path) ? [] : [o.path]));

  const q = query.trim().toLowerCase();
  const matches = (f: SchemaField) =>
    (!q || f.path.toLowerCase().includes(q) || (LABELS[f.path] ?? "").toLowerCase().includes(q)) &&
    (!changedOnly || (hasPath(values, f.path) && !sameValue(getPath(values, f.path), f.default)));

  return (
    <div>
      <datalist id="installed-models">{(installed ?? []).map((m) => <option key={m} value={m} />)}</datalist>
      <div className="tabs-inline" role="tablist">
        {([["options", t("config.editor.tab.options")], ["all", t("config.editor.tab.all")], ["yaml", "YAML"]] as const).map(([key, label]) => (
          <button key={key} type="button" role="tab" aria-selected={tab === key} className={tab === key ? "on" : ""} onClick={() => setTab(key)}>
            {label}
          </button>
        ))}
      </div>
      {schemaFailure && (
        <div className="banner bad">{t("config.editor.schemaFailure", { error: schemaFailure })}</div>
      )}
      {parseFailure && (
        <div className="banner bad">{t("config.editor.parseFailure", { error: parseFailure })}</div>
      )}
      {writeFailure && tab !== "yaml" && (
        <div className="banner bad">{t("config.editor.writeFailure", { error: writeFailure })}</div>
      )}
      {syntaxError && tab !== "yaml" && (
        <div className="banner bad">
          {syntaxError.line ? t("config.editor.syntaxErrorOnLine", { line: syntaxError.line }) : t("config.editor.syntaxError")}{" "}
          <button type="button" className="small" onClick={() => setTab("yaml")}>{t("config.editor.openYaml")}</button>
        </div>
      )}
      {!syntaxError && tab !== "yaml" && generalErrors.length > 0 && (
        <div className="banner bad">
          {generalErrors.map((e, i) => <div key={i}>{e.path ? <b className="mono">{e.path}: </b> : null}{e.message}</div>)}
        </div>
      )}
      {hasComments && !readOnly && tab !== "yaml" && (
        <p className="meta">{t("config.editor.commentsNotKept")}</p>
      )}

      {tab === "options" && sections.length > 0 && (
        <>
          <div className="row group-actions">
            <button type="button" className="small" onClick={() => setAll(COMMON_GROUPS.map((g) => `opt:${g.title}`), false)}>{t("config.editor.expandAll")}</button>
            <button type="button" className="small" onClick={() => setAll(COMMON_GROUPS.map((g) => `opt:${g.title}`), true)}>{t("config.editor.collapseAll")}</button>
          </div>
          <>
            {COMMON_GROUPS.map((group) =>
              renderGroup(
                `opt:${group.title}`,
                group.title,
                optionPaths(group),
                [
                  ...group.options.map((option) =>
                    "special" in option
                      ? option.special === "language-pair" ? renderLanguagePair() : renderGlossaryReview()
                      : fields.has(option.path) && !hidden.has(option.path) ? renderRow(fields.get(option.path)!) : null,
                  ),
                  renderHidden(group),
                ],
                group.help,
              ),
            )}
          </>
        </>
      )}

      {tab === "all" && (
        <>
          {/* Stays pinned under the page header while the settings scroll. */}
          <div className="settings-toolbar">
            <div className="row" style={{ marginTop: 0 }}>
              <input type="text" placeholder={t("config.editor.filterPlaceholder")} value={query} onChange={(e) => setQuery(e.target.value)} style={{ maxWidth: 320 }} />
              <label className="row meta" style={{ margin: 0 }}>
                <Switch checked={changedOnly} onChange={setChangedOnly} label={t("config.editor.changedOnlyLabel")} /> {t("config.editor.changedOnly")}
              </label>
              <span style={{ flex: 1 }} />
              <button type="button" className="small" onClick={() => setAll(sections.map((s) => `all:${s.key}`), false)}>{t("config.editor.expandAll")}</button>
              <button type="button" className="small" onClick={() => setAll(sections.map((s) => `all:${s.key}`), true)}>{t("config.editor.collapseAll")}</button>
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
          <>
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
          </>
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
