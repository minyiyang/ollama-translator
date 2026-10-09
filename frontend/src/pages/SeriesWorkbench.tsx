import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api } from "../api";
import { useDialog } from "../components/Dialog";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip } from "../components/ui";
import { Highlight } from "../components/ui";
import { rich, useT, type MessageKey, type Translate } from "../i18n";
import { glossaryCategoryLabel } from "../lib/enums";
import { FALLBACK_PAIR, langAttr, pairCodes, type GlossaryPair } from "../lib/languages";
import {
  WORKBENCH_VIEWS, matchesView, mentioningBooks, singleBookOf, suggestionEligible,
  type SuggestTask, type Term, type WorkbenchView,
} from "../lib/series";

type BookInfo = { job_id: string; volume: number | null; title: string };
type Evidence = {
  source: string;
  books: (BookInfo & {
    glossary: { target: string; category: string; note: string; evidence: string[] }[];
    mentions: number;
    snippets: string[];
  })[];
};
const PAGE = 150;
const volumeLabel = (t: Translate, book: BookInfo) => (book.volume ? t("series.volume", { volume: book.volume }) : book.job_id);

/** Every member book's view of a term: its translation, or how often its text mentions it. */
function BookLine({ term, books, lang }: { term: Term; books: BookInfo[]; lang: string }) {
  const t = useT();
  return (
    <div className="term-books">
      {books.map((book) => {
        const translations = term.books[book.job_id];
        const mentions = term.mentions?.[book.job_id] ?? 0;
        return (
          <span key={book.job_id} className={`book-cell ${translations ? "" : "absent"}`} title={`${book.title} (${book.job_id})`}>
            <span className="meta">{volumeLabel(t, book)}</span>{" "}
            {translations ? <span lang={lang}>{translations.join(" / ")}</span>
              : mentions ? <span className="meta">{t("series.bookLine.notInGlossary")}</span> : <span className="meta">—</span>}
            {mentions > 0 && <span className="meta"> · {mentions}×</span>}
          </span>
        );
      })}
    </div>
  );
}

/** Per book: glossary entries with their evidence, text mentions, and passages. */
function EvidencePanel({ seriesId, term, labels, lang }: { seriesId: string; term: Term; labels: Record<string, string>; lang: string }) {
  const t = useT();
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Evidence>(`/api/series/${encodeURIComponent(seriesId)}/term?id=${encodeURIComponent(term.term_id)}`)
      .then(setData).catch((e) => setError((e as Error).message));
  }, [seriesId, term.term_id]);
  if (error) return <div className="banner bad">{error}</div>;
  if (!data) return <p className="meta">{t("series.evidence.loading")}</p>;
  return (
    <div className="evidence">
      {data.books.map((book) => (
        <div key={book.job_id} className="evidence-book">
          <div>
            <b>{volumeLabel(t, book)}</b> {book.title} <span className="meta mono">{book.job_id}</span>
            <span className="meta"> · {book.mentions ? t(book.mentions >= 999 ? "series.evidence.mentionsCapped" : "series.evidence.mentions", { count: book.mentions }) : t("series.evidence.notMentioned")}</span>
          </div>
          {book.glossary.length ? book.glossary.map((entry, i) => (
            <div key={i} className="evidence-entry">
              {rich("series.evidence.glossary", { target: <span lang={lang}><b>{entry.target}</b></span>, category: <Chip>{glossaryCategoryLabel(entry.category, labels)}</Chip> })}
              {entry.note && <span className="meta"> {entry.note}</span>}
              {entry.evidence.map((sentence, j) => <div key={j} className="quote"><Highlight text={sentence} quote={data.source} /></div>)}
            </div>
          )) : <div className="meta">{t("series.evidence.notInGlossary")}</div>}
          {!book.glossary.length && book.snippets.map((snippet, j) => (
            <div key={j} className="quote"><Highlight text={snippet} quote={data.source} /></div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** The latest LLM run's output, refreshed while it runs. */
function RunLog({ seriesId, running }: { seriesId: string; running: boolean }) {
  const t = useT();
  const [lines, setLines] = useState<string[]>([]);
  const [file, setFile] = useState<string | null>(null);
  const box = useRef<HTMLDivElement>(null);
  const load = useCallback(() => {
    api<{ file: string | null; lines: string[] }>(`/api/series/${encodeURIComponent(seriesId)}/log`)
      .then((r) => { setLines(r.lines); setFile(r.file); }).catch(() => {});
  }, [seriesId]);
  useEffect(() => {
    load();
    if (!running) return;
    const timer = window.setInterval(load, 2000);
    return () => window.clearInterval(timer);
  }, [load, running]);
  useEffect(() => { if (box.current) box.current.scrollTop = box.current.scrollHeight; }, [lines]);
  const shown = lines.filter((line) => !line.includes("streaming —"));
  return (
    <div style={{ marginTop: 10 }}>
      <div className="meta">{file ? rich("series.log.file", { file: <span className="mono">{file}</span> }) : t("series.log.noRun")}</div>
      {file && (
        <div className="log logview series-log" ref={box}>
          {shown.length ? shown.map((line, i) => <div key={i} className={line.includes("[llm]") ? "l-llm" : /error|failed/i.test(line) ? "l-bad" : ""}>{line}</div>)
            : <span className="meta">{t("series.log.noOutput")}</span>}
        </div>
      )}
    </div>
  );
}

export type SeriesProcess = { label: string; running: boolean; outcome: string; output_tail: string } | null;

/** Each LLM task with its button label (which takes the count of eligible terms) and its tooltip. */
const TASKS: [SuggestTask, MessageKey, MessageKey][] = [
  ["conflicts", "series.task.conflicts", "series.task.conflictsHint"],
  ["generic", "series.task.generic", "series.task.genericHint"],
  ["promote", "series.task.promote", "series.task.promoteHint"],
];

const SUGGESTION: Record<NonNullable<Term["suggestion"]>["kind"], MessageKey> = {
  resolve: "series.suggestion.resolve",
  drop_generic: "series.suggestion.dropGeneric",
  promote: "series.suggestion.promote",
};

type View = {
  series_id: string;
  /** The series glossary's pair: an en/zh series' glossary is en-zh. */
  glossary_pair?: GlossaryPair;
  based_on: string | null;
  built_at: string;
  not_ready: string[];
  terms: Term[];
  books: string[];
  next_version: string;
  latest: string | null;
  publish: { keep: number; pending: number; added: string[]; changed: string[]; removed: string[]; stale: boolean };
  category_labels?: Record<string, string>;
  book_info?: BookInfo[];
};

// Each preset is the reason as the series records it, the same in every interface language, and the message that shows it.
const REASONS: Record<"keep" | "drop", [string, MessageKey][]> = {
  keep: [
    ["Canonical form for the series.", "series.reason.keep.canonical"],
    ["Main character, place, or item; share it across books.", "series.reason.keep.main"],
    ["The books agree after review.", "series.reason.keep.agreed"],
  ],
  drop: [
    ["Generic word, not a glossary term.", "series.reason.drop.generic"],
    ["Book-specific; keep it in that book's glossary.", "series.reason.drop.bookSpecific"],
  ],
};
const ORIGIN_LABEL: Record<Term["origin"], MessageKey> = {
  consensus: "series.origin.consensus",
  conflict: "series.origin.conflict",
  single_book: "series.origin.singleBook",
  carried: "series.origin.carried",
  manual: "series.origin.manual",
};
const DECISION_KIND: Record<Term["decision"], string> = { keep: "ok", drop: "", pending: "warn" };

/** Preset reasons grouped by decision, plus a custom one; returns the chosen text. */
function ReasonPicker({ name, value, onChange }: { name: string; value: string; onChange: (reason: string) => void }) {
  const t = useT();
  const [custom, setCustom] = useState(false);
  const [text, setText] = useState("");
  const presets = [[t("series.decision.keep"), REASONS.keep], [t("series.decision.drop"), REASONS.drop]] as const;
  return (
    <div className="reasons">
      {presets.map(([label, list]) => (
        <div className="rgroup" key={label}>
          <span className="meta">{label}</span>
          {list.map(([reason, key]) => (
            <label className="ropt" key={reason}>
              <input type="radio" name={name} checked={!custom && value === reason} onChange={() => { setCustom(false); onChange(reason); }} /> {t(key)}
            </label>
          ))}
        </div>
      ))}
      <div className="rgroup">
        <label className="ropt">
          <input type="radio" name={name} checked={custom} onChange={() => { setCustom(true); onChange(text); }} /> {t("series.reason.custom")}
        </label>
        <input type="text" className="customreason" placeholder={t("series.reason.customPlaceholder")} value={text}
          onFocus={() => { if (!custom) { setCustom(true); onChange(text); } }}
          onChange={(e) => { setText(e.target.value); setCustom(true); onChange(e.target.value); }} />
      </div>
    </div>
  );
}

function TermEditor({ term, categoryLabels, onDecide, busy, lang }: {
  term: Term;
  categoryLabels: Record<string, string>;
  busy: boolean;
  lang: string;
  onDecide: (decision: Term["decision"], reason: string, rendering: string | null, category: string | null, unlock: boolean) => void;
}) {
  const t = useT();
  const [rendering, setRendering] = useState(term.target);
  const [category, setCategory] = useState(term.category);
  const [reason, setReason] = useState("");
  const [unlock, setUnlock] = useState(false);
  const variants = [...new Set([term.target, ...Object.values(term.books).flat()])];
  const changed = rendering.trim() !== term.target;
  const recategorized = category !== term.category;
  const reasonOk = reason.trim().length >= 3;
  const categories = Object.keys(categoryLabels).length ? Object.keys(categoryLabels) : [term.category];
  const decide = (decision: Term["decision"]) =>
    onDecide(decision, reason, changed && decision === "keep" ? rendering.trim() : null, recategorized ? category : null, unlock);
  return (
    <div className="term-editor">
      <div className="row" style={{ margin: 0 }}>
        <span className="meta">{t("series.editor.pick")}</span>
        {variants.map((v) => (
          <button key={v} className={`small ${v === rendering ? "on" : ""}`} lang={lang} onClick={() => setRendering(v)}>{v}</button>
        ))}
        <input type="text" lang={lang} className="term-input" value={rendering} aria-label={t("series.editor.translation")} onChange={(e) => setRendering(e.target.value)} />
        <label className="meta" htmlFor={`category-${term.term_id}`}>{t("series.editor.category")}</label>
        <select id={`category-${term.term_id}`} className="term-category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((value) => <option key={value} value={value}>{glossaryCategoryLabel(value, categoryLabels)}</option>)}
        </select>
      </div>
      <h3 className="term-subhead">{rich("series.reason.heading", { hint: (chunks) => <span className="meta">{chunks}</span> })}</h3>
      <ReasonPicker name={`reason-${term.term_id}`} value={reason} onChange={setReason} />
      {term.locked_from && (
        <label className="meta" title={t("series.editor.unlockHint")}>
          <input type="checkbox" checked={unlock} onChange={(e) => setUnlock(e.target.checked)} /> {t("series.editor.unlock", { version: term.locked_from })}
        </label>
      )}
      <div className="row" style={{ margin: "8px 0 0" }}>
        <button className="primary small" disabled={busy || !reasonOk || !(changed || recategorized) || !rendering.trim()}
          title={t("series.editor.saveHint")}
          onClick={() => onDecide(term.decision, reason, changed ? rendering.trim() : null, recategorized ? category : null, unlock)}>
          {t("series.editor.save")}
        </button>
        <button className="small" disabled={busy || !reasonOk || !rendering.trim()} onClick={() => decide("keep")}>
          {changed || recategorized ? t("series.decision.keepChanged") : t("series.decision.keep")}
        </button>
        <button className="small" disabled={busy || !reasonOk} onClick={() => decide("drop")}>{t("series.decision.drop")}</button>
        <button className="small" disabled={busy || !reasonOk} onClick={() => decide("pending")}>{t("series.decision.leavePending")}</button>
        {!reasonOk && <span className="meta">{t("series.reason.missing")}</span>}
      </div>
    </div>
  );
}

export function WorkbenchTab({ seriesId, process, onChanged, onPublished }: {
  seriesId: string;
  /** The series' background LLM run, if any. */
  process: SeriesProcess;
  /** A decision changed the counts the series page shows. */
  onChanged: () => void;
  onPublished: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const ask = useDialog();
  const [data, setData] = useState<View | null>(null);
  const [error, setError] = useState("");
  const [view, setView] = useState<WorkbenchView>("pending");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [checked, setChecked] = useState<string[]>([]);
  const [bulkReason, setBulkReason] = useState("");
  const [busy, setBusy] = useState(false);
  const [evidenceOf, setEvidenceOf] = useState<string | null>(null);
  const [logOpen, setLogOpen] = useState(!!process?.running);
  const [limit, setLimit] = useState(PAGE);
  useEffect(() => { if (process?.running) setLogOpen(true); }, [process?.running]);
  useEffect(() => setLimit(PAGE), [view, query]);

  const load = useCallback(() => {
    api<View>(`/api/series/${encodeURIComponent(seriesId)}/workbench`)
      .then((view) => { setData(view); setError(""); })
      .catch((e) => setError((e as Error).message));
  }, [seriesId]);
  useEffect(load, [load]);

  const decide = async (
    termIds: string[], decision: Term["decision"], reason: string, rendering: string | null, category: string | null = null, unlock = false,
  ) => {
    setBusy(true);
    try {
      setData(await api<View>(`/api/series/${encodeURIComponent(seriesId)}/workbench/decide`, {
        term_ids: termIds, decision, reason, target: rendering, category, unlock,
      }));
      setOpen(null);
      setChecked([]);
      setBulkReason("");
      toast("ok", t("series.decision.saved", { decision, count: termIds.length }));
      onChanged();
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const runTask = async (task: SuggestTask) => {
    setBusy(true);
    try {
      await api(`/api/series/${encodeURIComponent(seriesId)}/suggest`, { task });
      toast("ok", t("series.task.started"));
      onChanged();
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const answer = async (termIds: string[], accept: boolean) => {
    setBusy(true);
    try {
      setData(await api<View>(`/api/series/${encodeURIComponent(seriesId)}/workbench/suggestions`, { term_ids: termIds, accept }));
      onChanged();
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const publish = async () => {
    if (!data) return;
    const p = data.publish;
    const b = (chunks: ReactNode[]) => <b>{chunks}</b>;
    const list = (key: MessageKey, names: string[]) =>
      names.length ? <p>{rich(key, { b, count: names.length, version: data.latest, names: <span className="meta">{names.slice(0, 12).join(", ")}{names.length > 12 ? ", …" : ""}</span> })}</p> : null;
    const choice = await ask(
      t("series.publish.confirm.title", { version: data.next_version }),
      <>
        <p>{rich("series.publish.confirm.kept", { b, count: p.keep, version: data.next_version })}</p>
        {list("series.publish.confirm.added", p.added)}
        {list("series.publish.confirm.changed", p.changed)}
        {list("series.publish.confirm.removed", p.removed)}
        {p.pending > 0 && <div className="banner warn">{t("series.publish.confirm.pending", { count: p.pending })}</div>}
        <p className="meta">{t("series.publish.confirm.note", { version: data.next_version })}</p>
      </>,
      [{ value: "cancel", label: t("common.cancel") }, { value: "publish", label: t("series.publishVersion", { version: data.next_version }), primary: true }],
    );
    if (choice !== "publish") return;
    setBusy(true);
    try {
      await api(`/api/series/${encodeURIComponent(seriesId)}/publish`, {});
      toast("ok", t("series.publish.done", { version: data.next_version }));
      onPublished();
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const counts = useMemo(() => {
    const result: Record<string, number> = {};
    const terms = data?.terms ?? [];
    WORKBENCH_VIEWS.forEach(([key]) => { result[key] = terms.filter((t) => matchesView(t, key)).length; });
    terms.forEach((t) => { const job = singleBookOf(t); if (job) result[`book:${job}`] = (result[`book:${job}`] ?? 0) + 1; });
    return result;
  }, [data]);
  const books: BookInfo[] = data?.book_info ?? (data?.books ?? []).map((job_id) => ({ job_id, volume: null, title: job_id }));

  if (error) return <div className="banner bad">{error}</div>;
  if (!data) return <p className="meta">{t("common.loading")}</p>;

  const pair = data.glossary_pair ?? FALLBACK_PAIR;
  const [sourceLang, targetLang] = pairCodes(pair.pair).map(langAttr);
  const needle = query.trim().toLowerCase();
  const shown = data.terms.filter((t) => matchesView(t, view) && (!needle
    || t.source.toLowerCase().includes(needle) || t.target.toLowerCase().includes(needle)));

  const sidebar = (
    <>
      <input type="text" className="side-search" placeholder={t("series.workbench.search")} value={query} onChange={(e) => setQuery(e.target.value)} />
      {WORKBENCH_VIEWS.map(([key, label]) => (
        <div key={key}>
          <SideItem active={view === key} count={counts[key]} onClick={() => { setView(key); setChecked([]); }}>{t(label)}</SideItem>
          {key === "single_book" && books.map((book) => (
            <div key={book.job_id} className="side-sub">
              <SideItem active={view === `book:${book.job_id}`} count={counts[`book:${book.job_id}`] ?? 0}
                onClick={() => { setView(`book:${book.job_id}`); setChecked([]); }}>
                <span title={book.job_id}>{volumeLabel(t, book)} · {book.title}</span>
              </SideItem>
            </div>
          ))}
        </div>
      ))}
    </>
  );

  return (
    <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:series-workbench" label={t("series.workbench.views")}>
      <section className="card">
        <div className="row" style={{ margin: 0, justifyContent: "space-between" }}>
          <span>
            {rich(data.based_on ? "series.workbench.summaryOnTop" : "series.workbench.summary", { b: (chunks) => <b>{chunks}</b>, terms: data.terms.length, base: data.based_on, keep: data.publish.keep })}
            {data.publish.pending ? <> · <Chip kind="warn">{t("series.pendingCount", { count: data.publish.pending })}</Chip></> : null}
          </span>
          <button className="primary" disabled={busy || data.publish.stale || !data.publish.keep}
            title={data.publish.stale ? t("series.publish.staleHint") : ""}
            onClick={publish}>{t("series.publishVersion", { version: data.next_version })}</button>
        </div>
        <div className="row" style={{ margin: "10px 0 0" }}>
          <span className="meta">{t("series.task.label")}</span>
          {TASKS.map(([task, label, tip]) => {
            const n = data.terms.filter((t) => suggestionEligible(t, task)).length;
            return (
              <button key={task} className="small" title={t(tip)} disabled={busy || !!process?.running || !n}
                onClick={() => runTask(task)}>{t(label, { count: n })}</button>
            );
          })}
          <span className="meta">{t("series.task.hint")}</span>
        </div>
        <p className="meta" style={{ margin: "8px 0 0" }}>
          {t("series.workbench.saveNote", { version: data.next_version })}{" "}
          <button className="linklike" onClick={() => setLogOpen(!logOpen)}>{logOpen ? t("series.log.hide") : t("series.log.show")}</button>
        </p>
        {logOpen && <RunLog seriesId={seriesId} running={!!process?.running} />}
        {process?.running && <div className="banner info" style={{ marginTop: 10 }}>{t("series.workbench.running", { label: process.label })}</div>}
        {process && !process.running && process.outcome === "failed" && (
          <div className="banner bad" style={{ marginTop: 10 }}>{t("series.workbench.runFailed", { label: process.label })}<pre>{process.output_tail}</pre></div>
        )}
        {data.publish.stale && <div className="banner warn" style={{ marginTop: 10 }}>{t("series.publish.stale", { version: data.latest })}</div>}
        {data.not_ready.length > 0 && <p className="meta">{t("series.workbench.notIncluded", { jobs: data.not_ready.join(", ") })}</p>}
      </section>

      {checked.length > 0 && (
        <section className="card bulk-bar">
          <div className="row" style={{ margin: 0, width: "100%" }}>
            {rich("series.workbench.selected", { b: (chunks) => <b>{chunks}</b>, count: checked.length })}
            <button className="small" disabled={busy || bulkReason.trim().length < 3} onClick={() => decide(checked, "keep", bulkReason, null)}>{t("series.decision.keep")}</button>
            <button className="small" disabled={busy || bulkReason.trim().length < 3} onClick={() => decide(checked, "drop", bulkReason, null)}>{t("series.decision.drop")}</button>
            <button className="small" onClick={() => setChecked([])}>{t("series.workbench.clear")}</button>
            {bulkReason.trim().length < 3 && <span className="meta">{t("series.reason.missingBulk")}</span>}
          </div>
          <ReasonPicker name="bulk-reason" value={bulkReason} onChange={setBulkReason} />
        </section>
      )}

      {view === "suggested" && shown.length > 0 && (
        <section className="card bulk-bar">
          {rich("series.suggestion.shown", { b: (chunks) => <b>{chunks}</b>, count: shown.length })}
          <button className="small primary" disabled={busy} onClick={() => answer(shown.map((t) => t.term_id), true)}>{t("series.suggestion.acceptAll")}</button>
          <button className="small" disabled={busy} onClick={() => answer(shown.map((t) => t.term_id), false)}>{t("series.suggestion.rejectAll")}</button>
        </section>
      )}
      {view === "elsewhere" && (
        <p className="meta">{rich("series.workbench.elsewhereIntro", { b: (chunks) => <b>{chunks}</b> })}</p>
      )}
      {shown.length === 0 && <p className="meta">{t("series.workbench.empty")}</p>}
      {shown.slice(0, limit).map((term) => (
        <section key={term.term_id} className={`card term ${term.decision}`}>
          <div className="term-head">
            <input type="checkbox" aria-label={t("series.term.select", { term: term.source })} checked={checked.includes(term.term_id)}
              onChange={(e) => setChecked((old) => (e.target.checked ? [...old, term.term_id] : old.filter((id) => id !== term.term_id)))} />
            <b lang={sourceLang}>{term.source}</b>
            <span lang={targetLang} className="term-zh">{term.target}</span>
            <Chip>{glossaryCategoryLabel(term.category, data.category_labels)}</Chip>
            {term.origin !== "carried" && <Chip kind={term.origin === "conflict" ? "warn" : undefined}>{t(ORIGIN_LABEL[term.origin])}</Chip>}
            {term.origin === "single_book" && mentioningBooks(term).length >= 2 && (
              <Chip kind="warn" title={t("series.term.inOtherBooksHint")}>
                {t("series.term.inOtherBooks", { count: mentioningBooks(term).length })}
              </Chip>
            )}
            {term.locked_from && <Chip>{t("series.term.publishedIn", { version: term.locked_from })}</Chip>}
            <span className="spacer" />
            <Chip kind={DECISION_KIND[term.decision]}>{t(term.decided_by !== "rule" ? "series.term.decisionByYou" : "series.term.decision", { decision: term.decision })}</Chip>
            <button className="small" onClick={() => setEvidenceOf(evidenceOf === term.term_id ? null : term.term_id)}>
              {evidenceOf === term.term_id ? t("series.term.hideEvidence") : t("series.term.evidence")}
            </button>
            <button className="small" onClick={() => setOpen(open === term.term_id ? null : term.term_id)}>{open === term.term_id ? t("common.close") : t("series.term.decide")}</button>
          </div>
          <BookLine term={term} books={books} lang={targetLang} />
          {evidenceOf === term.term_id && <EvidencePanel seriesId={seriesId} term={term} labels={data.category_labels ?? {}} lang={targetLang} />}
          {term.reason && <div className="meta">{term.reason}</div>}
          {term.suggestion && (
            <div className="suggestion">
              <span>{rich(SUGGESTION[term.suggestion.kind], { b: (chunks) => <b>{chunks}</b>, target: term.suggestion.target })} <span className="meta">{term.suggestion.rationale}</span></span>
              <span className="row" style={{ margin: 0 }}>
                <button className="small primary" disabled={busy} onClick={() => answer([term.term_id], true)}>{t("series.suggestion.accept")}</button>
                <button className="small" disabled={busy} onClick={() => answer([term.term_id], false)}>{t("series.suggestion.reject")}</button>
              </span>
            </div>
          )}
          {open === term.term_id && (
            <TermEditor term={term} busy={busy} categoryLabels={data.category_labels ?? {}} lang={targetLang}
              onDecide={(decision, reason, rendering, category, unlock) => decide([term.term_id], decision, reason, rendering, category, unlock)} />
          )}
        </section>
      ))}
      {shown.length > limit && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button onClick={() => setLimit(limit + PAGE)}>{t("series.workbench.showMore", { more: Math.min(PAGE, shown.length - limit), remaining: shown.length - limit })}</button>
        </div>
      )}
    </SideLayout>
  );
}
