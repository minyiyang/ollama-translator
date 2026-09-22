import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api } from "../api";
import { useDialog } from "../components/Dialog";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip } from "../components/ui";
import { Highlight } from "../components/ui";
import {
  WORKBENCH_VIEWS, matchesView, mentioningBooks, singleBookOf, suggestionEligible,
  type SuggestTask, type Term, type WorkbenchView,
} from "../lib/series";

type BookInfo = { job_id: string; volume: number | null; title: string };
type Evidence = {
  english: string;
  books: (BookInfo & {
    glossary: { chinese: string; category: string; note: string; evidence: string[] }[];
    mentions: number;
    snippets: string[];
  })[];
};
const PAGE = 150;
const volumeLabel = (book: BookInfo) => (book.volume ? `Vol. ${book.volume}` : book.job_id);

/** Every member book's view of a term: its translation, or how often its text mentions it. */
function BookLine({ term, books }: { term: Term; books: BookInfo[] }) {
  return (
    <div className="term-books">
      {books.map((book) => {
        const translations = term.books[book.job_id];
        const mentions = term.mentions?.[book.job_id] ?? 0;
        return (
          <span key={book.job_id} className={`book-cell ${translations ? "" : "absent"}`} title={`${book.title} (${book.job_id})`}>
            <span className="meta">{volumeLabel(book)}</span>{" "}
            {translations ? <span lang="zh-CN">{translations.join(" / ")}</span>
              : mentions ? <span className="meta">not in glossary</span> : <span className="meta">—</span>}
            {mentions > 0 && <span className="meta"> · {mentions}×</span>}
          </span>
        );
      })}
    </div>
  );
}

/** Per book: glossary entries with their evidence, text mentions, and passages. */
function EvidencePanel({ seriesId, term, labels }: { seriesId: string; term: Term; labels: Record<string, string> }) {
  const [data, setData] = useState<Evidence | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<Evidence>(`/api/series/${encodeURIComponent(seriesId)}/term?id=${encodeURIComponent(term.term_id)}`)
      .then(setData).catch((e) => setError((e as Error).message));
  }, [seriesId, term.term_id]);
  if (error) return <div className="banner bad">{error}</div>;
  if (!data) return <p className="meta">Loading evidence…</p>;
  return (
    <div className="evidence">
      {data.books.map((book) => (
        <div key={book.job_id} className="evidence-book">
          <div>
            <b>{volumeLabel(book)}</b> {book.title} <span className="meta mono">{book.job_id}</span>
            <span className="meta"> · {book.mentions ? `${book.mentions}${book.mentions >= 999 ? "+" : ""} mentions in the text` : "not mentioned in the text"}</span>
          </div>
          {book.glossary.length ? book.glossary.map((entry, i) => (
            <div key={i} className="evidence-entry">
              Glossary: <span lang="zh-CN"><b>{entry.chinese}</b></span> <Chip>{labels[entry.category] ?? entry.category}</Chip>
              {entry.note && <span className="meta"> {entry.note}</span>}
              {entry.evidence.map((sentence, j) => <div key={j} className="quote"><Highlight text={sentence} quote={data.english} /></div>)}
            </div>
          )) : <div className="meta">Not in this book's glossary.</div>}
          {!book.glossary.length && book.snippets.map((snippet, j) => (
            <div key={j} className="quote"><Highlight text={snippet} quote={data.english} /></div>
          ))}
        </div>
      ))}
    </div>
  );
}

/** The latest LLM run's output, refreshed while it runs. */
function RunLog({ seriesId, running }: { seriesId: string; running: boolean }) {
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
      <div className="meta">{file ? <>Log: <span className="mono">{file}</span></> : "No LLM run yet."}</div>
      {file && (
        <div className="log logview series-log" ref={box}>
          {shown.length ? shown.map((line, i) => <div key={i} className={line.includes("[llm]") ? "l-llm" : /error|failed/i.test(line) ? "l-bad" : ""}>{line}</div>)
            : <span className="meta">No output yet.</span>}
        </div>
      )}
    </div>
  );
}

export type SeriesProcess = { label: string; running: boolean; outcome: string; output_tail: string } | null;

const TASKS: [SuggestTask, string, string][] = [
  ["conflicts", "Resolve conflicts", "Pick one existing book translation for each pending conflict"],
  ["generic", "Flag generic terms", "Suggest dropping ordinary words that are not series terms"],
  ["promote", "Suggest promotions", "Suggest single-book terms worth sharing across the series"],
];

function suggestionText(term: Term): string {
  const s = term.suggestion;
  if (!s) return "";
  if (s.kind === "resolve") return `use ${s.chinese} for the whole series`;
  if (s.kind === "drop_generic") return "drop it: an ordinary word, not a series term";
  return "promote it to the series glossary";
}

type View = {
  series_id: string;
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

const REASONS: Record<"keep" | "drop", string[]> = {
  keep: [
    "Canonical form for the series.",
    "Main character, place, or item; share it across books.",
    "The books agree after review.",
  ],
  drop: [
    "Generic word, not a glossary term.",
    "Book-specific; keep it in that book's glossary.",
  ],
};
const ORIGIN_LABEL: Record<Term["origin"], string> = {
  consensus: "agreed",
  conflict: "conflict",
  single_book: "single book",
  carried: "published",
  manual: "added",
};
const DECISION_KIND: Record<Term["decision"], string> = { keep: "ok", drop: "", pending: "warn" };

/** Preset reasons grouped by decision, plus a custom one; returns the chosen text. */
function ReasonPicker({ name, value, onChange }: { name: string; value: string; onChange: (reason: string) => void }) {
  const [custom, setCustom] = useState(false);
  const [text, setText] = useState("");
  const presets: [string, string[]][] = [["Keep", REASONS.keep], ["Drop", REASONS.drop]];
  return (
    <div className="reasons">
      {presets.map(([label, list]) => (
        <div className="rgroup" key={label}>
          <span className="meta">{label}</span>
          {list.map((reason) => (
            <label className="ropt" key={reason}>
              <input type="radio" name={name} checked={!custom && value === reason} onChange={() => { setCustom(false); onChange(reason); }} /> {reason}
            </label>
          ))}
        </div>
      ))}
      <div className="rgroup">
        <label className="ropt">
          <input type="radio" name={name} checked={custom} onChange={() => { setCustom(true); onChange(text); }} /> Custom reason
        </label>
        <input type="text" className="customreason" placeholder="Describe why (at least 3 characters)" value={text}
          onFocus={() => { if (!custom) { setCustom(true); onChange(text); } }}
          onChange={(e) => { setText(e.target.value); setCustom(true); onChange(e.target.value); }} />
      </div>
    </div>
  );
}

function TermEditor({ term, categoryLabels, onDecide, busy }: {
  term: Term;
  categoryLabels: Record<string, string>;
  busy: boolean;
  onDecide: (decision: Term["decision"], reason: string, chinese: string | null, category: string | null, unlock: boolean) => void;
}) {
  const [chinese, setChinese] = useState(term.chinese);
  const [category, setCategory] = useState(term.category);
  const [reason, setReason] = useState("");
  const [unlock, setUnlock] = useState(false);
  const variants = [...new Set([term.chinese, ...Object.values(term.books).flat()])];
  const changed = chinese.trim() !== term.chinese;
  const recategorized = category !== term.category;
  const reasonOk = reason.trim().length >= 3;
  const categories = Object.keys(categoryLabels).length ? Object.keys(categoryLabels) : [term.category];
  const decide = (decision: Term["decision"]) =>
    onDecide(decision, reason, changed && decision === "keep" ? chinese.trim() : null, recategorized ? category : null, unlock);
  return (
    <div className="term-editor">
      <div className="row" style={{ margin: 0 }}>
        <span className="meta">Pick:</span>
        {variants.map((v) => (
          <button key={v} className={`small ${v === chinese ? "on" : ""}`} lang="zh-CN" onClick={() => setChinese(v)}>{v}</button>
        ))}
        <input type="text" lang="zh-CN" className="term-input" value={chinese} aria-label="Series translation" onChange={(e) => setChinese(e.target.value)} />
        <label className="meta" htmlFor={`category-${term.term_id}`}>Category</label>
        <select id={`category-${term.term_id}`} className="term-category" value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((value) => <option key={value} value={value}>{categoryLabels[value] ?? value}</option>)}
        </select>
      </div>
      <h3 className="term-subhead">Reason <span className="meta">(required)</span></h3>
      <ReasonPicker name={`reason-${term.term_id}`} value={reason} onChange={setReason} />
      {term.locked_from && (
        <label className="meta" title="Changing or dropping a published term only affects books pinned to the new version">
          <input type="checkbox" checked={unlock} onChange={(e) => setUnlock(e.target.checked)} /> change the term published in {term.locked_from}
        </label>
      )}
      <div className="row" style={{ margin: "8px 0 0" }}>
        <button className="primary small" disabled={busy || !reasonOk || !(changed || recategorized) || !chinese.trim()}
          title="Save the new translation or category and keep the current decision"
          onClick={() => onDecide(term.decision, reason, changed ? chinese.trim() : null, recategorized ? category : null, unlock)}>
          Save changes
        </button>
        <button className="small" disabled={busy || !reasonOk || !chinese.trim()} onClick={() => decide("keep")}>
          Keep{changed || recategorized ? " with these changes" : ""}
        </button>
        <button className="small" disabled={busy || !reasonOk} onClick={() => decide("drop")}>Drop</button>
        <button className="small" disabled={busy || !reasonOk} onClick={() => decide("pending")}>Leave pending</button>
        {!reasonOk && <span className="meta">Pick or write a reason first.</span>}
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
    api<View>(`/api/series/${encodeURIComponent(seriesId)}/workbench`).then(setData).catch((e) => setError((e as Error).message));
  }, [seriesId]);
  useEffect(load, [load]);

  const decide = async (
    termIds: string[], decision: Term["decision"], reason: string, chinese: string | null, category: string | null = null, unlock = false,
  ) => {
    setBusy(true);
    try {
      setData(await api<View>(`/api/series/${encodeURIComponent(seriesId)}/workbench/decide`, {
        term_ids: termIds, decision, reason, chinese, category, unlock,
      }));
      setOpen(null);
      setChecked([]);
      setBulkReason("");
      toast("ok", `Saved: ${termIds.length === 1 ? "1 term" : `${termIds.length} terms`} ${decision === "pending" ? "left pending" : decision === "keep" ? "kept" : "dropped"}.`);
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
      toast("ok", "LLM suggestions started; they appear here when the run finishes.");
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
    const list = (label: string, names: string[]) =>
      names.length ? <p><b>{names.length}</b> {label}: <span className="meta">{names.slice(0, 12).join(", ")}{names.length > 12 ? ", …" : ""}</span></p> : null;
    const choice = await ask(
      `Publish ${data.next_version}?`,
      <>
        <p><b>{p.keep}</b> kept terms become series glossary {data.next_version}. It is never changed afterwards.</p>
        {list("added", p.added)}
        {list(`changed from ${data.latest}`, p.changed)}
        {list(`removed from ${data.latest}`, p.removed)}
        {p.pending > 0 && <div className="banner warn">{p.pending} pending term{p.pending === 1 ? " is" : "s are"} left out and stay in their book glossaries.</div>}
        <p className="meta">
          Books at their glossary gate then review their glossary synchronized to {data.next_version}. Books pinned to an
          earlier version are not changed.
        </p>
      </>,
      [{ value: "cancel", label: "Cancel" }, { value: "publish", label: `Publish ${data.next_version}`, primary: true }],
    );
    if (choice !== "publish") return;
    setBusy(true);
    try {
      await api(`/api/series/${encodeURIComponent(seriesId)}/publish`, {});
      toast("ok", `Published ${data.next_version}.`);
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
  if (!data) return <p className="meta">Loading…</p>;

  const needle = query.trim().toLowerCase();
  const shown = data.terms.filter((t) => matchesView(t, view) && (!needle
    || t.english.toLowerCase().includes(needle) || t.chinese.includes(query.trim())));

  const sidebar = (
    <>
      <input type="text" className="side-search" placeholder="Search terms" value={query} onChange={(e) => setQuery(e.target.value)} />
      {WORKBENCH_VIEWS.map(([key, label]) => (
        <div key={key}>
          <SideItem active={view === key} count={counts[key]} onClick={() => { setView(key); setChecked([]); }}>{label}</SideItem>
          {key === "single_book" && books.map((book) => (
            <div key={book.job_id} className="side-sub">
              <SideItem active={view === `book:${book.job_id}`} count={counts[`book:${book.job_id}`] ?? 0}
                onClick={() => { setView(`book:${book.job_id}`); setChecked([]); }}>
                <span title={book.job_id}>{volumeLabel(book)} · {book.title}</span>
              </SideItem>
            </div>
          ))}
        </div>
      ))}
    </>
  );

  return (
    <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:series-workbench" label="Views">
      <section className="card">
        <div className="row" style={{ margin: 0, justifyContent: "space-between" }}>
          <span>
            <b>{data.terms.length}</b> candidate terms{data.based_on ? ` on top of ${data.based_on}` : ""}
            {" · "}<b>{data.publish.keep}</b> to publish{data.publish.pending ? <> · <Chip kind="warn">{data.publish.pending} pending</Chip></> : null}
          </span>
          <button className="primary" disabled={busy || data.publish.stale || !data.publish.keep}
            title={data.publish.stale ? "A newer version was published; rebuild the candidate on the Books tab" : ""}
            onClick={publish}>Publish {data.next_version}</button>
        </div>
        <div className="row" style={{ margin: "10px 0 0" }}>
          <span className="meta">LLM:</span>
          {TASKS.map(([task, label, tip]) => {
            const n = data.terms.filter((t) => suggestionEligible(t, task)).length;
            return (
              <button key={task} className="small" title={tip} disabled={busy || !!process?.running || !n}
                onClick={() => runTask(task)}>{label} ({n})</button>
            );
          })}
          <span className="meta">Suggestions change nothing until you accept them.</span>
        </div>
        <p className="meta" style={{ margin: "8px 0 0" }}>
          Every decision is saved as soon as you make it and survives reloads and rebuilds. Publishing freezes the kept terms
          into {data.next_version}.{" "}
          <button className="linklike" onClick={() => setLogOpen(!logOpen)}>{logOpen ? "Hide" : "Show"} LLM run log</button>
        </p>
        {logOpen && <RunLog seriesId={seriesId} running={!!process?.running} />}
        {process?.running && <div className="banner info" style={{ marginTop: 10 }}>{process.label} is running…</div>}
        {process && !process.running && process.outcome === "failed" && (
          <div className="banner bad" style={{ marginTop: 10 }}>The {process.label} run failed:<pre>{process.output_tail}</pre></div>
        )}
        {data.publish.stale && <div className="banner warn" style={{ marginTop: 10 }}>This candidate predates {data.latest}; rebuild it on the Books tab before publishing.</div>}
        {data.not_ready.length > 0 && <p className="meta">Not included yet (glossary not ready): {data.not_ready.join(", ")}.</p>}
      </section>

      {checked.length > 0 && (
        <section className="card bulk-bar">
          <div className="row" style={{ margin: 0, width: "100%" }}>
            <b>{checked.length}</b> selected
            <button className="small" disabled={busy || bulkReason.trim().length < 3} onClick={() => decide(checked, "keep", bulkReason, null)}>Keep</button>
            <button className="small" disabled={busy || bulkReason.trim().length < 3} onClick={() => decide(checked, "drop", bulkReason, null)}>Drop</button>
            <button className="small" onClick={() => setChecked([])}>Clear</button>
            {bulkReason.trim().length < 3 && <span className="meta">Pick or write a reason for all selected terms.</span>}
          </div>
          <ReasonPicker name="bulk-reason" value={bulkReason} onChange={setBulkReason} />
        </section>
      )}

      {view === "suggested" && shown.length > 0 && (
        <section className="card bulk-bar">
          <b>{shown.length}</b> suggestion{shown.length === 1 ? "" : "s"} shown
          <button className="small primary" disabled={busy} onClick={() => answer(shown.map((t) => t.term_id), true)}>Accept all shown</button>
          <button className="small" disabled={busy} onClick={() => answer(shown.map((t) => t.term_id), false)}>Reject all shown</button>
        </section>
      )}
      {view === "elsewhere" && (
        <p className="meta">
          Each term below is in <b>one book's glossary only</b>, but its English also appears in the text of other books
          (the orange chip counts them), so the other books translate it ad hoc. These are the terms worth promoting to the
          series glossary. Open <b>Evidence</b> to read each book's passages before deciding.
        </p>
      )}
      {shown.length === 0 && <p className="meta">No terms in this view.</p>}
      {shown.slice(0, limit).map((term) => (
        <section key={term.term_id} className={`card term ${term.decision}`}>
          <div className="term-head">
            <input type="checkbox" aria-label={`Select ${term.english}`} checked={checked.includes(term.term_id)}
              onChange={(e) => setChecked((old) => (e.target.checked ? [...old, term.term_id] : old.filter((id) => id !== term.term_id)))} />
            <b>{term.english}</b>
            <span lang="zh-CN" className="term-zh">{term.chinese}</span>
            <Chip>{data.category_labels?.[term.category] ?? term.category}</Chip>
            {term.origin !== "carried" && <Chip kind={term.origin === "conflict" ? "warn" : undefined}>{ORIGIN_LABEL[term.origin]}</Chip>}
            {term.origin === "single_book" && mentioningBooks(term).length >= 2 && (
              <Chip kind="warn" title="The English term also appears in other books' text; open Evidence">
                in {mentioningBooks(term).length} books&rsquo; text
              </Chip>
            )}
            {term.locked_from && <Chip>published in {term.locked_from}</Chip>}
            <span className="spacer" />
            <Chip kind={DECISION_KIND[term.decision]}>{term.decision}{term.decided_by !== "rule" ? " · by you" : ""}</Chip>
            <button className="small" onClick={() => setEvidenceOf(evidenceOf === term.term_id ? null : term.term_id)}>
              {evidenceOf === term.term_id ? "Hide evidence" : "Evidence"}
            </button>
            <button className="small" onClick={() => setOpen(open === term.term_id ? null : term.term_id)}>{open === term.term_id ? "Close" : "Decide"}</button>
          </div>
          <BookLine term={term} books={books} />
          {evidenceOf === term.term_id && <EvidencePanel seriesId={seriesId} term={term} labels={data.category_labels ?? {}} />}
          {term.reason && <div className="meta">{term.reason}</div>}
          {term.suggestion && (
            <div className="suggestion">
              <span><b>LLM suggests:</b> {suggestionText(term)}. <span className="meta">{term.suggestion.rationale}</span></span>
              <span className="row" style={{ margin: 0 }}>
                <button className="small primary" disabled={busy} onClick={() => answer([term.term_id], true)}>Accept</button>
                <button className="small" disabled={busy} onClick={() => answer([term.term_id], false)}>Reject</button>
              </span>
            </div>
          )}
          {open === term.term_id && (
            <TermEditor term={term} busy={busy} categoryLabels={data.category_labels ?? {}}
              onDecide={(decision, reason, chinese, category, unlock) => decide([term.term_id], decision, reason, chinese, category, unlock)} />
          )}
        </section>
      ))}
      {shown.length > limit && (
        <div className="row" style={{ justifyContent: "center" }}>
          <button onClick={() => setLimit(limit + PAGE)}>Show {Math.min(PAGE, shown.length - limit)} more of {shown.length - limit}</button>
        </div>
      )}
    </SideLayout>
  );
}
