import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { LanguagePairPicker } from "../components/LanguagePairPicker";
import { Card, Chip } from "../components/ui";
import { directionLabel, relativeTime } from "../lib/format";
import { rich, useT, type MessageKey } from "../i18n";
import { FALLBACK_PAIR, langAttr, pairCodes, type GlossaryPair } from "../lib/languages";
import { seriesIdFromName } from "../lib/series";
import { NewJobDialog, type Setup } from "./JobsPage";
import { WorkbenchTab, type SeriesProcess } from "./SeriesWorkbench";

type SeriesRow = { series_id: string; name: string; direction: string; books: number; latest: string | null; pending: number | null };
type Book = { job_id: string; volume: number | null; added_at: string; glossary: string; version: string | null };
type Version = { version: string; created_at: string; source_jobs: string[]; term_count: number; based_on: string | null };
type Addable = { job_id: string; source: string; glossary: string };
type Workbench = { based_on: string | null; built_at: string; terms: number; pending: number; keep: number } | null;
type Detail = {
  series_id: string;
  name: string;
  direction: string;
  books: Book[];
  versions: Version[];
  workbench: Workbench;
  addable: Addable[];
  process: SeriesProcess;
};
type Entry = { source: string; target: string; category: string };

const seriesApi = <T,>(id: string, path: string, body?: unknown) =>
  api<T>(`/api/series/${encodeURIComponent(id)}/${path}`, body);

const GLOSSARY_STATE: Record<string, [string, MessageKey]> = {
  approved: ["completed", "series.glossaryState.approved"],
  resolved: ["paused", "series.glossaryState.resolved"],
  "not ready": ["pending", "series.glossaryState.notReady"],
  draft: ["draft", "series.glossaryState.draft"],
  missing: ["failed", "series.glossaryState.missing"],
};

function NewSeriesDialog({ directions, onClose }: { directions: string[]; onClose: () => void }) {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const [name, setName] = useState("");
  const [seriesId, setSeriesId] = useState("");
  const [idTouched, setIdTouched] = useState(false);
  const [direction, setDirection] = useState(directions[0] ?? "en-zh");
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (!idTouched) setSeriesId(seriesIdFromName(name)); }, [name, idTouched]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const create = async () => {
    setBusy(true);
    try {
      await api("/api/series", { series_id: seriesId, name, direction });
      navigate(`/series/${encodeURIComponent(seriesId)}`);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
      setBusy(false);
    }
  };
  const idOk = /^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$/.test(seriesId);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal" role="dialog" aria-modal="true" aria-labelledby="new-series-title">
        <h2 id="new-series-title">{t("series.new.title")}</h2>
        <div className="field">
          <label htmlFor="series-name">{t("series.new.name")}</label>
          <input id="series-name" type="text" value={name} placeholder={t("series.new.namePlaceholder")} onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label htmlFor="series-id">{t("series.new.id")}</label>
          <input id="series-id" type="text" className="mono" value={seriesId} onChange={(e) => { setIdTouched(true); setSeriesId(e.target.value); }} />
          <span className="hint">{rich("series.new.idHint", { path: (chunks) => <span className="mono">{chunks}</span>, id: seriesId || "…" })}</span>
        </div>
        <div className="field">
          <span className="label-text">{t("series.new.languages")}</span>
          <LanguagePairPicker value={direction} onChange={setDirection} />
          <span className="hint">{t("series.new.languagesHint")}</span>
        </div>
        <div className="row dialog-actions">
          <button onClick={onClose}>{t("common.cancel")}</button>
          <button className="primary" disabled={busy || !idOk || !name.trim()} onClick={create}>{t("series.new.create")}</button>
        </div>
      </section>
    </div>
  );
}

export function SeriesListPage() {
  const t = useT();
  const [rows, setRows] = useState<SeriesRow[] | null>(null);
  const [directions, setDirections] = useState<string[]>([]);
  const [error, setError] = useState("");
  const [adding, setAdding] = useState(false);
  useEffect(() => {
    api<{ series: SeriesRow[]; directions: string[] }>("/api/series")
      .then((r) => { setRows(r.series); setDirections(r.directions); })
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <Shell>
      <main className="page">
        <Card title={t("series.list.title")}>
          <p className="meta">{t("series.list.intro")}</p>
          {error && <div className="banner bad">{error}</div>}
          {!rows && !error && <p className="meta">{t("common.loading")}</p>}
          {rows && !rows.length && <p className="meta">{t("series.list.empty")}</p>}
          {rows && rows.length > 0 && (
            <table className="grid">
              <thead><tr><th>{t("series.list.column.series")}</th><th>{t("series.list.column.type")}</th><th className="num">{t("series.list.column.books")}</th><th>{t("series.list.column.latest")}</th><th>{t("series.list.column.workbench")}</th></tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.series_id}>
                    <td><Link to={`/series/${encodeURIComponent(row.series_id)}`}>{row.name}</Link> <span className="meta mono">{row.series_id}</span></td>
                    <td className="mono nowrap">{directionLabel(row.direction)}</td>
                    <td className="num">{row.books}</td>
                    <td>{row.latest ?? <span className="meta">{t("series.list.nonePublished")}</span>}</td>
                    <td>{row.pending === null ? <span className="meta">—</span> : row.pending ? <Chip kind="warn">{t("series.pendingCount", { count: row.pending })}</Chip> : <Chip kind="ok">{t("series.list.readyToPublish")}</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button className="add-job" disabled={!directions.length} onClick={() => setAdding(true)}>{t("series.list.new")}</button>
        </Card>
      </main>
      {adding && <NewSeriesDialog directions={directions} onClose={() => setAdding(false)} />}
    </Shell>
  );
}

function BooksTab({ detail, onChange, onCurate }: { detail: Detail; onChange: (d: Detail) => void; onCurate: () => void }) {
  const t = useT();
  const toast = useToast();
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<Setup | null>(null);
  const newBook = async () => {
    try { setSetup(await api<Setup>("/api/setup")); } catch (e) { toast("bad", (e as Error).message, 0); }
  };
  /** Resolves to whether the request went through. */
  const run = async (path: string, body: object, done: string) => {
    setBusy(true);
    try {
      onChange(await seriesApi<Detail>(detail.series_id, path, body));
      toast("ok", done);
      return true;
    } catch (e) {
      toast("bad", (e as Error).message, 0);
      return false;
    } finally {
      setBusy(false);
    }
  };
  const latest = detail.versions.at(-1)?.version;
  const ready = detail.books.filter((b) => b.glossary === "approved" || b.glossary === "resolved").length;
  const wb = detail.workbench;

  return (
    <>
      <Card title={t("series.books.title", { count: detail.books.length })}>
        {detail.books.length === 0 ? <p className="meta">{t("series.books.empty")}</p> : (
          <table className="grid">
            <thead><tr><th className="num">{t("series.books.column.volume")}</th><th>{t("series.books.column.job")}</th><th>{t("series.books.column.glossary")}</th><th>{t("series.books.column.pinned")}</th><th /></tr></thead>
            <tbody>
              {detail.books.map((book) => {
                const state = GLOSSARY_STATE[book.glossary];
                const [kind, label] = state ? [state[0], t(state[1])] : ["pending", book.glossary];
                return (
                  <tr key={book.job_id}>
                    <td className="num">{book.volume ?? ""}</td>
                    <td><Link className="mono" to={`/jobs/${encodeURIComponent(book.job_id)}/${book.glossary === "draft" ? "config" : "glossary"}`}>{book.job_id}</Link></td>
                    <td><Chip kind={kind}>{label}</Chip></td>
                    <td>
                      {book.version ?? (book.glossary === "resolved" && latest
                        ? <Link to={`/jobs/${encodeURIComponent(book.job_id)}/glossary`}>{t("series.books.approveWith", { version: latest })}</Link>
                        : <span className="meta">{t("series.books.notPinned")}</span>)}
                      {book.version && latest && book.version !== latest && <span className="meta"> · {t("series.books.versionAvailable", { version: latest })}</span>}
                    </td>
                    <td>
                      {!book.version && (
                        <button className="small" disabled={busy} title={t("series.books.removeHint")}
                          onClick={() => run("books/remove", { job_id: book.job_id }, t("series.books.removed", { job: book.job_id }))}>{t("common.remove")}</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <h2 style={{ marginTop: 16 }}>{t("series.books.addHeading")}</h2>
        <div className="row">
          <button className="primary" onClick={newBook}>{t("series.books.new")}</button>
          <span className="meta">{t("series.books.newHint")}</span>
        </div>
        {detail.addable.length === 0 ? (
          <p className="meta">{t("series.books.noneAddable", { direction: directionLabel(detail.direction) })}</p>
        ) : (
          <>
            <p className="meta">{t("series.books.addExisting")}</p>
          <>
            <div className="pick-list">
              {detail.addable.map((job) => (
                <label key={job.job_id} className="ropt">
                  <input type="checkbox" checked={picked.includes(job.job_id)}
                    onChange={(e) => setPicked((old) => (e.target.checked ? [...old, job.job_id] : old.filter((id) => id !== job.job_id)))} />
                  <span className="mono">{job.job_id}</span> <span className="meta">{job.source} · {job.glossary}</span>
                </label>
              ))}
            </div>
            <div className="row">
              <button className="primary" disabled={busy || !picked.length}
                onClick={() => run("books", { job_ids: picked }, t("series.books.added", { count: picked.length })).then((added) => { if (added) setPicked([]); })}>
                {t("series.books.addAsNext", { count: picked.length })}
              </button>
              <span className="meta">{t("series.books.orderHint")}</span>
            </div>
          </>
          </>
        )}
        {setup && (
          <NewJobDialog setup={setup} series={{ series_id: detail.series_id, name: detail.name }} onClose={() => setSetup(null)} />
        )}
      </Card>

      <Card title={t("series.candidate.title")}>
        <p className="meta">
          {t("series.candidate.intro")}
          {latest && ` ${t("series.candidate.carried", { version: latest })}`}
        </p>
        {wb ? (
          <p>
            {rich(wb.based_on ? "series.candidate.builtOnTop" : "series.candidate.built", {
              ago: relativeTime(wb.built_at), base: wb.based_on, terms: wb.terms, keep: wb.keep, pending: wb.pending,
              b: (chunks) => <b>{chunks}</b>, chip: (chunks) => <Chip kind="warn">{chunks}</Chip>,
            })}
          </p>
        ) : <p className="meta">{t("series.candidate.notBuilt")}</p>}
        <div className="row">
          <button className="primary" disabled={busy || !ready}
            title={ready ? t("series.candidate.buildHint") : t("series.candidate.buildBlocked")}
            onClick={() => run("build", {}, t("series.candidate.builtToast"))}>{wb ? t("series.candidate.rebuild") : t("series.candidate.build")}</button>
          <span className="meta">{t("series.candidate.ready", { ready, total: detail.books.length })}</span>
        </div>
        {wb && (
          <p className="meta">
            {rich("series.candidate.review", { tab: (chunks) => <button className="linklike" onClick={onCurate}>{chunks}</button> })}
          </p>
        )}
      </Card>
    </>
  );
}

function VersionsTab({ detail }: { detail: Detail }) {
  const t = useT();
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const [pair, setPair] = useState<GlossaryPair>(FALLBACK_PAIR);
  const pins = (version: string) => detail.books.filter((b) => b.version === version).map((b) => b.job_id);
  const toggle = async (version: string) => {
    if (open === version) { setOpen(null); return; }
    try {
      const res = await seriesApi<{ glossary: { entries: Entry[] }; glossary_pair?: GlossaryPair }>(detail.series_id, `version?v=${encodeURIComponent(version)}`);
      setEntries(res.glossary.entries);
      setPair(res.glossary_pair ?? FALLBACK_PAIR);
      setOpen(version);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    }
  };
  if (!detail.versions.length) return <Card title={t("series.versions.title")}><p className="meta">{t("series.versions.empty")}</p></Card>;
  return (
    <Card title={t("series.versions.title")}>
      <p className="meta">{t("series.versions.intro")}</p>
      <table className="grid">
        <thead><tr><th>{t("series.versions.column.version")}</th><th>{t("series.versions.column.published")}</th><th className="num">{t("series.versions.column.terms")}</th><th>{t("series.versions.column.fromBooks")}</th><th>{t("series.versions.column.pinnedBy")}</th><th /></tr></thead>
        <tbody>
          {[...detail.versions].reverse().map((v) => (
            <tr key={v.version}>
              <td className="mono">{v.version}{v.based_on && <span className="meta"> ← {v.based_on}</span>}</td>
              <td className="meta">{relativeTime(v.created_at)}</td>
              <td className="num">{v.term_count}</td>
              <td className="meta">{v.source_jobs.join(", ") || t("series.versions.imported")}</td>
              <td className="meta">{pins(v.version).join(", ") || "—"}</td>
              <td><button className="small" onClick={() => toggle(v.version)}>{open === v.version ? t("series.versions.hideTerms") : t("series.versions.showTerms")}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && (
        <>
          <h2 style={{ marginTop: 16 }}>{t("series.versions.termsHeading", { version: open, count: entries.length })}</h2>
          <table className="grid">
            <thead><tr><th>{pair.source}</th><th>{pair.target}</th><th>{t("series.versions.column.category")}</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.source}><td lang={langAttr(pairCodes(pair.pair)[0])}>{e.source}</td><td lang={langAttr(pairCodes(pair.pair)[1])}>{e.target}</td><td className="meta">{e.category}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

export function SeriesPage() {
  const t = useT();
  const { seriesId = "" } = useParams();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"books" | "workbench" | "versions">("books");
  const load = useCallback(() => {
    seriesApi<Detail>(seriesId, "detail")
      .then((next) => { setDetail(next); setError(""); })
      .catch((e) => setError((e as Error).message));
  }, [seriesId]);
  useEffect(load, [load]);
  // Follow a background LLM run; the workbench reloads when it ends.
  const running = !!detail?.process?.running;
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(load, 3000);
    return () => window.clearInterval(timer);
  }, [running, load]);

  return (
    <Shell crumb={detail?.name ?? seriesId}>
      <main className="page">
        {error && <div className="banner bad">{error}</div>}
        {!detail && !error && <p className="meta">{t("common.loading")}</p>}
        {detail && (
          <>
            <div className="seghead">
              <span className="title">{detail.name}</span>
              <span className="meta mono">{detail.series_id}</span>
              <Chip>{directionLabel(detail.direction)}</Chip>
              <Chip kind={detail.versions.length ? "ok" : undefined}>{detail.versions.at(-1)?.version ?? t("series.noVersionYet")}</Chip>
            </div>
            <div className="segmented" role="tablist" style={{ marginBottom: 12 }}>
              <button role="tab" aria-selected={tab === "books"} className={tab === "books" ? "on" : ""} onClick={() => setTab("books")}>{t("series.tab.books")}</button>
              <button role="tab" aria-selected={tab === "workbench"} className={tab === "workbench" ? "on" : ""} disabled={!detail.workbench} title={detail.workbench ? "" : t("series.tab.workbenchDisabled")}
                onClick={() => setTab("workbench")}>{t("series.tab.workbench", { pending: detail.workbench?.pending ?? 0 })}</button>
              <button role="tab" aria-selected={tab === "versions"} className={tab === "versions" ? "on" : ""} onClick={() => setTab("versions")}>{t("series.tab.versions", { count: detail.versions.length })}</button>
            </div>
            {tab === "books" && <BooksTab detail={detail} onChange={setDetail} onCurate={() => setTab("workbench")} />}
            {tab === "workbench" && detail.workbench && (
              <WorkbenchTab key={running ? "running" : "idle"} seriesId={detail.series_id} process={detail.process}
                onChanged={load} onPublished={() => { load(); setTab("versions"); }} />
            )}
            {tab === "versions" && <VersionsTab detail={detail} />}
          </>
        )}
      </main>
    </Shell>
  );
}
