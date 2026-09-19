import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, uploadSource } from "../api";
import { BookCard, type BookInfo } from "../components/BookCard";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Bar, Card, Chip } from "../components/ui";
import { directionLabel, outputUrl, relativeTime } from "../lib/format";
import { stageLabel } from "../lib/stages";

type Job = {
  job_id: string; overall: string; source: string; direction: string; downloadable: boolean;
  current_stage: string; completed: number; total: number; updated: string;
};
type Setup = { configs: { name: string }[]; config_dir: string; runs: string; template: string; jobs: Job[] };

const PAGE_SIZE = 10;
const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const stem = (name: string) => name.replace(/\.ya?ml$/i, "");

function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  const [draft, setDraft] = useState(String(page));
  useEffect(() => setDraft(String(page)), [page]);
  const go = (value: number) => onPage(Math.min(pages, Math.max(1, value)));
  return (
    <div className="pages">
      <button className="small" disabled={page <= 1} onClick={() => go(1)} title="First page">«</button>
      <button className="small" disabled={page <= 1} onClick={() => go(page - 1)} title="Previous page">‹</button>
      <span className="meta">Page</span>
      <input type="number" min={1} max={pages} value={draft} aria-label="Page number"
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => { if (e.key === "Enter") go(Number(draft) || 1); }}
        onBlur={() => go(Number(draft) || 1)} />
      <span className="meta">of {pages}</span>
      <button className="small" disabled={page >= pages} onClick={() => go(page + 1)} title="Next page">›</button>
      <button className="small" disabled={page >= pages} onClick={() => go(pages)} title="Last page">»</button>
    </div>
  );
}

function JobsTable({ jobs }: { jobs: Job[] }) {
  return (
    <table className="grid">
      <thead>
        <tr><th>Job</th><th>Source</th><th>Type</th><th>Status</th><th>Stage</th><th>Progress</th><th>Updated</th><th /></tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const base = `/jobs/${encodeURIComponent(job.job_id)}`;
          const draft = job.overall === "draft" || job.overall === "starting";
          const waiting =
            job.overall === "paused" && job.current_stage === "approve_glossary" ? ["glossary", "Review glossary →"]
            : job.overall === "paused" && job.current_stage === "compile" ? ["review", "Open final review →"]
            : null;
          return (
            <tr key={job.job_id}>
              <td><Link className="mono" to={`${base}/${draft ? "config" : "progress"}`}>{job.job_id}</Link></td>
              <td>{job.source}</td>
              <td className="mono nowrap" title={job.direction || "direction not set"}>{directionLabel(job.direction)}</td>
              <td><Chip kind={job.overall}>{job.overall}</Chip></td>
              <td>
                {draft ? <span className="meta">not started</span> : job.current_stage ? stageLabel(job.current_stage) : "—"}
                {waiting && <div><Link to={`${base}/${waiting[0]}`}>{waiting[1]}</Link></div>}
              </td>
              <td style={{ minWidth: 140 }}>
                <Bar percent={(100 * job.completed) / job.total} />
                <span className="meta">{job.completed}/{job.total} stages</span>
              </td>
              <td className="meta">{relativeTime(job.updated)}</td>
              <td>
                {job.downloadable && (
                  <a className="button small" href={outputUrl(job.job_id)} download title="Download the translated book">⤓ Download</a>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function NewJobDialog({ setup, onClose }: { setup: Setup; onClose: () => void }) {
  const toast = useToast();
  const navigate = useNavigate();
  const [source, setSource] = useState("");
  const [book, setBook] = useState<BookInfo | null>(null);
  const [configName, setConfigName] = useState("");
  const [jobId, setJobId] = useState("");
  const [configTouched, setConfigTouched] = useState(false);
  const [jobTouched, setJobTouched] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  // Defaults follow the source until the user edits them: book name -> config name -> job id.
  useEffect(() => {
    if (!source) return;
    api<{ config: string; job_id: string }>(`/api/jobs/suggest?source=${encodeURIComponent(source)}`).then((s) => {
      if (!configTouched) setConfigName(s.config);
    });
  }, [source, configTouched]);
  useEffect(() => { if (!jobTouched) setJobId(stem(configName)); }, [configName, jobTouched]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!/\.(epub|rtf)$/i.test(file.name)) { toast("bad", "Choose an .epub or .rtf file."); return; }
    setUploading(true);
    try {
      const path = await uploadSource(file);
      setBook(await api<BookInfo>(`/api/book?path=${encodeURIComponent(path)}`));
      setSource(path);
    }
    catch (e) { toast("bad", (e as Error).message, 0); }
    finally { setUploading(false); }
  };

  // Dropping onto the empty zone or onto the picked book's card both (re)place the source.
  const dropHandlers = {
    onDragOver: (e: DragEvent) => { e.preventDefault(); setOver(true); },
    onDragLeave: () => setOver(false),
    onDrop: (e: DragEvent) => { e.preventDefault(); setOver(false); upload(e.dataTransfer.files[0]); },
  };

  const create = async () => {
    setBusy(true);
    try {
      const draft = await api<{ job_id: string; created_config: boolean }>("/api/jobs/new", { source, config: configName, job_id: jobId });
      toast("ok", draft.created_config ? `Created ${configName} from ${fileName(setup.template) || "an empty config"}.` : `Using existing ${configName}.`);
      navigate(`/jobs/${encodeURIComponent(draft.job_id)}/config`);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
      setBusy(false);
    }
  };

  const existing = setup.configs.some((c) => c.name === configName);
  const nameOk = /^[A-Za-z0-9][A-Za-z0-9._-]*\.ya?ml$/.test(configName);
  const taken = setup.jobs.some((j) => j.job_id === jobId);

  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <section className="card modal" role="dialog" aria-modal="true" aria-labelledby="new-job-title">
        <h2 id="new-job-title">New translation job</h2>

        <div className="field">
          <label>Source EPUB or RTF</label>
          <input ref={picker} type="file" accept=".epub,.rtf" hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          {book && !uploading ? (
            <div {...dropHandlers} className={over ? "drop-over" : ""}>
              <BookCard book={book} onChange={() => picker.current?.click()} />
            </div>
          ) : (
            <div {...dropHandlers} className={`dropzone ${over ? "over" : ""}`}>
              {uploading ? "Uploading and reading the book…" : <>Drop an EPUB or RTF here, or{" "}
                <button type="button" className="small" onClick={() => picker.current?.click()}>Browse…</button></>}
            </div>
          )}
          <span className="hint">The browser uploads a copy into the dashboard's runs folder; the path shown is that copy.</span>
        </div>

        <div className="field">
          <label htmlFor="config-name">Config file name</label>
          <input id="config-name" type="text" value={configName} spellCheck={false} placeholder="my-book.yaml"
            onChange={(e) => { setConfigTouched(true); setConfigName(e.target.value); }} />
          <span className="hint">
            {!configName ? "Pick a source to get a suggested name."
              : !nameOk ? "Use a simple file name ending in .yaml."
              : existing ? `${configName} already exists in ${setup.config_dir}; the job uses it as is.`
              : `A new ${configName} is created in ${setup.config_dir} from ${fileName(setup.template) || "an empty config"}; you adjust it on the Config tab.`}
          </span>
        </div>

        <div className="field">
          <label htmlFor="job-id">Job ID</label>
          <input id="job-id" type="text" value={jobId} spellCheck={false}
            onChange={(e) => { setJobTouched(true); setJobId(e.target.value); }} />
          <span className="hint">{taken ? "A job with this ID already exists." : "Defaults to the config file name. Used as the workspace folder name."}</span>
        </div>

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose}>Cancel</button>
          <button className="primary" disabled={busy || uploading || !source || !nameOk || !jobId || taken} onClick={create}>
            Create job
          </button>
        </div>
      </section>
    </div>
  );
}

export function JobsPage() {
  const [setup, setSetup] = useState<Setup | null>(null);
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(() => api<Setup>("/api/setup").then(setSetup), []);
  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => refresh().catch(() => {}), 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const jobs = setup?.jobs ?? [];
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const shown = jobs.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  return (
    <Shell>
      <main className="page">
        <Card title="Jobs">
          {!setup && <p className="meta">Loading…</p>}
          {setup && !jobs.length && <p className="meta">No jobs yet in <span className="mono">{setup.runs}</span>.</p>}
          {shown.length > 0 && <JobsTable jobs={shown} />}
          {jobs.length > 0 && (
            <div className="pager">
              <span className="meta">
                {(current - 1) * PAGE_SIZE + 1}–{(current - 1) * PAGE_SIZE + shown.length} of {jobs.length} jobs
              </span>
              <Pager page={current} pages={pages} onPage={setPage} />
            </div>
          )}
          <button className="add-job" disabled={!setup} onClick={() => setAdding(true)}>+ Add new job</button>
        </Card>
      </main>
      {adding && setup && <NewJobDialog setup={setup} onClose={() => setAdding(false)} />}
    </Shell>
  );
}
