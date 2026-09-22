import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Card, Chip } from "../components/ui";
import { directionLabel, relativeTime } from "../lib/format";
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
type Entry = { english: string; chinese: string; category: string };

const seriesApi = <T,>(id: string, path: string, body?: unknown) =>
  api<T>(`/api/series/${encodeURIComponent(id)}/${path}`, body);

const GLOSSARY_STATE: Record<string, [string, string]> = {
  approved: ["completed", "approved"],
  resolved: ["paused", "at glossary gate"],
  "not ready": ["pending", "glossary not ready"],
  draft: ["draft", "draft · not started"],
  missing: ["failed", "job missing"],
};

function NewSeriesDialog({ directions, onClose }: { directions: string[]; onClose: () => void }) {
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
        <h2 id="new-series-title">New series</h2>
        <div className="field">
          <label htmlFor="series-name">Name</label>
          <input id="series-name" type="text" value={name} placeholder="The Qel Cycle" onChange={(e) => setName(e.target.value)} autoFocus />
        </div>
        <div className="field">
          <label htmlFor="series-id">Series ID</label>
          <input id="series-id" type="text" className="mono" value={seriesId} onChange={(e) => { setIdTouched(true); setSeriesId(e.target.value); }} />
          <span className="hint">Letters, digits, dot, dash, underscore. Stored under <span className="mono">runs/.series/{seriesId || "…"}</span>.</span>
        </div>
        <div className="field">
          <label htmlFor="series-direction">Translation direction</label>
          <select id="series-direction" value={direction} onChange={(e) => setDirection(e.target.value)}>
            {directions.map((d) => <option key={d} value={d}>{directionLabel(d)}</option>)}
          </select>
          <span className="hint">Every book in the series must translate in this direction.</span>
        </div>
        <div className="row dialog-actions">
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || !idOk} onClick={create}>Create series</button>
        </div>
      </section>
    </div>
  );
}

export function SeriesListPage() {
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
        <Card title="Series">
          <p className="meta">
            A series shares one versioned glossary across its books. Each book pins a version, so a later volume
            never changes an already translated book.
          </p>
          {error && <div className="banner bad">{error}</div>}
          {!rows && !error && <p className="meta">Loading…</p>}
          {rows && !rows.length && <p className="meta">No series yet.</p>}
          {rows && rows.length > 0 && (
            <table className="grid">
              <thead><tr><th>Series</th><th>Type</th><th className="num">Books</th><th>Latest version</th><th>Workbench</th></tr></thead>
              <tbody>
                {rows.map((row) => (
                  <tr key={row.series_id}>
                    <td><Link to={`/series/${encodeURIComponent(row.series_id)}`}>{row.name}</Link> <span className="meta mono">{row.series_id}</span></td>
                    <td className="mono nowrap">{directionLabel(row.direction)}</td>
                    <td className="num">{row.books}</td>
                    <td>{row.latest ?? <span className="meta">none published</span>}</td>
                    <td>{row.pending === null ? <span className="meta">—</span> : row.pending ? <Chip kind="warn">{row.pending} pending</Chip> : <Chip kind="ok">ready to publish</Chip>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <button className="add-job" disabled={!directions.length} onClick={() => setAdding(true)}>+ New series</button>
        </Card>
      </main>
      {adding && <NewSeriesDialog directions={directions} onClose={() => setAdding(false)} />}
    </Shell>
  );
}

function BooksTab({ detail, onChange, onCurate }: { detail: Detail; onChange: (d: Detail) => void; onCurate: () => void }) {
  const toast = useToast();
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [setup, setSetup] = useState<Setup | null>(null);
  const newBook = async () => {
    try { setSetup(await api<Setup>("/api/setup")); } catch (e) { toast("bad", (e as Error).message, 0); }
  };
  const run = async (path: string, body: object, done: string) => {
    setBusy(true);
    try {
      onChange(await seriesApi<Detail>(detail.series_id, path, body));
      toast("ok", done);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };
  const latest = detail.versions.at(-1)?.version;
  const ready = detail.books.filter((b) => b.glossary === "approved" || b.glossary === "resolved").length;
  const wb = detail.workbench;

  return (
    <>
      <Card title={`Books (${detail.books.length})`}>
        {detail.books.length === 0 ? <p className="meta">No books yet. Add jobs below.</p> : (
          <table className="grid">
            <thead><tr><th className="num">Vol.</th><th>Job</th><th>Glossary</th><th>Pinned version</th><th /></tr></thead>
            <tbody>
              {detail.books.map((book) => {
                const [kind, label] = GLOSSARY_STATE[book.glossary] ?? ["pending", book.glossary];
                return (
                  <tr key={book.job_id}>
                    <td className="num">{book.volume ?? ""}</td>
                    <td><Link className="mono" to={`/jobs/${encodeURIComponent(book.job_id)}/${book.glossary === "draft" ? "config" : "glossary"}`}>{book.job_id}</Link></td>
                    <td><Chip kind={kind}>{label}</Chip></td>
                    <td>
                      {book.version ?? (book.glossary === "resolved" && latest
                        ? <Link to={`/jobs/${encodeURIComponent(book.job_id)}/glossary`}>approve with {latest} →</Link>
                        : <span className="meta">not pinned</span>)}
                      {book.version && latest && book.version !== latest && <span className="meta"> · {latest} available</span>}
                    </td>
                    <td>
                      {!book.version && (
                        <button className="small" disabled={busy} title="Only a book not pinned to a version can leave the series"
                          onClick={() => run("books/remove", { job_id: book.job_id }, `${book.job_id} removed.`)}>Remove</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        <h2 style={{ marginTop: 16 }}>Add books</h2>
        <div className="row">
          <button className="primary" onClick={newBook}>+ New book</button>
          <span className="meta">Upload a book: it becomes a new job in this series, then you validate and start it on its Config tab.</span>
        </div>
        {detail.addable.length === 0 ? (
          <p className="meta">No existing {directionLabel(detail.direction)} jobs outside a series to add.</p>
        ) : (
          <>
            <p className="meta">Or add existing jobs:</p>
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
                onClick={() => run("books", { job_ids: picked }, `Added ${picked.length} book${picked.length === 1 ? "" : "s"}.`).then(() => setPicked([]))}>
                Add {picked.length || ""} as next volume{picked.length === 1 ? "" : "s"}
              </button>
              <span className="meta">Volumes follow the order you tick them.</span>
            </div>
          </>
          </>
        )}
        {setup && (
          <NewJobDialog setup={setup} series={{ series_id: detail.series_id, name: detail.name }} onClose={() => setSetup(null)} />
        )}
      </Card>

      <Card title="Candidate glossary">
        <p className="meta">
          Collects the glossaries of books at the glossary gate (resolved) or already approved. Terms agreed by 2+ books are
          kept, disagreements wait for a decision, single-book terms stay in their book.
          {latest && ` Terms of ${latest} are carried and locked.`}
        </p>
        {wb ? (
          <p>
            Built {relativeTime(wb.built_at)}{wb.based_on ? ` on top of ${wb.based_on}` : ""}:{" "}
            <b>{wb.terms}</b> terms, <b>{wb.keep}</b> kept, {wb.pending ? <Chip kind="warn">{wb.pending} pending</Chip> : "none pending"}.
          </p>
        ) : <p className="meta">Not built yet.</p>}
        <div className="row">
          <button className="primary" disabled={busy || !ready}
            title={ready ? "Deterministic, no LLM calls" : "No book has a resolved or approved glossary yet"}
            onClick={() => run("build", {}, "Candidate built.")}>{wb ? "Rebuild candidate" : "Build candidate"}</button>
          <span className="meta">{ready} of {detail.books.length} books ready.</span>
        </div>
        {wb && (
          <p className="meta">
            Review the candidate and publish it on the <button className="linklike" onClick={onCurate}>Workbench</button> tab.
          </p>
        )}
      </Card>
    </>
  );
}

function VersionsTab({ detail }: { detail: Detail }) {
  const toast = useToast();
  const [open, setOpen] = useState<string | null>(null);
  const [entries, setEntries] = useState<Entry[]>([]);
  const pins = (version: string) => detail.books.filter((b) => b.version === version).map((b) => b.job_id);
  const toggle = async (version: string) => {
    if (open === version) { setOpen(null); return; }
    try {
      const res = await seriesApi<{ glossary: { entries: Entry[] } }>(detail.series_id, `version?v=${encodeURIComponent(version)}`);
      setEntries(res.glossary.entries);
      setOpen(version);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    }
  };
  if (!detail.versions.length) return <Card title="Versions"><p className="meta">Nothing published yet.</p></Card>;
  return (
    <Card title="Versions">
      <p className="meta">Published versions are never changed. Each book keeps the version it is pinned to.</p>
      <table className="grid">
        <thead><tr><th>Version</th><th>Published</th><th className="num">Terms</th><th>From books</th><th>Pinned by</th><th /></tr></thead>
        <tbody>
          {[...detail.versions].reverse().map((v) => (
            <tr key={v.version}>
              <td className="mono">{v.version}{v.based_on && <span className="meta"> ← {v.based_on}</span>}</td>
              <td className="meta">{relativeTime(v.created_at)}</td>
              <td className="num">{v.term_count}</td>
              <td className="meta">{v.source_jobs.join(", ") || "imported"}</td>
              <td className="meta">{pins(v.version).join(", ") || "—"}</td>
              <td><button className="small" onClick={() => toggle(v.version)}>{open === v.version ? "Hide" : "Terms"}</button></td>
            </tr>
          ))}
        </tbody>
      </table>
      {open && (
        <>
          <h2 style={{ marginTop: 16 }}>{open} terms ({entries.length})</h2>
          <table className="grid">
            <thead><tr><th>Source</th><th>Translation</th><th>Category</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.english}><td>{e.english}</td><td lang="zh-CN">{e.chinese}</td><td className="meta">{e.category}</td></tr>
              ))}
            </tbody>
          </table>
        </>
      )}
    </Card>
  );
}

export function SeriesPage() {
  const { seriesId = "" } = useParams();
  const [detail, setDetail] = useState<Detail | null>(null);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"books" | "workbench" | "versions">("books");
  const load = useCallback(() => {
    seriesApi<Detail>(seriesId, "detail").then(setDetail).catch((e) => setError((e as Error).message));
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
        {!detail && !error && <p className="meta">Loading…</p>}
        {detail && (
          <>
            <div className="seghead">
              <span className="title">{detail.name}</span>
              <span className="meta mono">{detail.series_id}</span>
              <Chip>{directionLabel(detail.direction)}</Chip>
              <Chip kind={detail.versions.length ? "ok" : undefined}>{detail.versions.at(-1)?.version ?? "no version yet"}</Chip>
            </div>
            <div className="segmented" style={{ marginBottom: 12 }}>
              <button className={tab === "books" ? "on" : ""} onClick={() => setTab("books")}>Books</button>
              <button className={tab === "workbench" ? "on" : ""} disabled={!detail.workbench} title={detail.workbench ? "" : "Build the candidate on the Books tab first"}
                onClick={() => setTab("workbench")}>Workbench{detail.workbench?.pending ? ` (${detail.workbench.pending} pending)` : ""}</button>
              <button className={tab === "versions" ? "on" : ""} onClick={() => setTab("versions")}>Versions ({detail.versions.length})</button>
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
