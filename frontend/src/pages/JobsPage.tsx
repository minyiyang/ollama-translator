import { useCallback, useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, uploadSource } from "../api";
import { BookCard, type BookInfo } from "../components/BookCard";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Bar, Card, Chip } from "../components/ui";
import { rich, useT } from "../i18n";
import { directionLabel, isSourceBook, jobTypeLabel, outputUrl, relativeTime, SOURCE_ACCEPT, sourceKinds, type JobType } from "../lib/format";
import { stageLabel } from "../lib/stages";

type Job = {
  job_id: string; overall: string; source: string; direction: string; downloadable: boolean;
  /** A book, or a subtitle file; a server from before subtitle jobs does not say. */
  job_type?: JobType;
  current_stage: string; completed: number; total: number; updated: string;
};
export type Setup = { configs: { name: string }[]; config_dir: string; runs: string; template: string; jobs: Job[] };

const PAGE_SIZE = 10;
const fileName = (path: string) => path.split(/[\\/]/).pop() ?? path;
const stem = (name: string) => name.replace(/\.ya?ml$/i, "");

function Pager({ page, pages, onPage }: { page: number; pages: number; onPage: (page: number) => void }) {
  const t = useT();
  const [draft, setDraft] = useState(String(page));
  useEffect(() => setDraft(String(page)), [page]);
  const go = (value: number) => onPage(Math.min(pages, Math.max(1, value)));
  return (
    <div className="pages">
      <button className="small" disabled={page <= 1} onClick={() => go(1)} title={t("jobs.pager.first")}>«</button>
      <button className="small" disabled={page <= 1} onClick={() => go(page - 1)} title={t("jobs.pager.previous")}>‹</button>
      {rich("jobs.pager.pageOf", {
        pages,
        meta: (chunks) => <span className="meta">{chunks}</span>,
        input: (
          <input type="number" min={1} max={pages} value={draft} aria-label={t("jobs.pager.pageNumber")}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") go(Number(draft) || 1); }}
            onBlur={() => go(Number(draft) || 1)} />
        ),
      })}
      <button className="small" disabled={page >= pages} onClick={() => go(page + 1)} title={t("jobs.pager.next")}>›</button>
      <button className="small" disabled={page >= pages} onClick={() => go(pages)} title={t("jobs.pager.last")}>»</button>
    </div>
  );
}

function JobsTable({ jobs }: { jobs: Job[] }) {
  const t = useT();
  return (
    <table className="grid">
      <thead>
        <tr><th>{t("jobs.table.job")}</th><th>{t("jobs.table.source")}</th><th>{t("jobs.table.kind")}</th><th>{t("jobs.table.languages")}</th><th>{t("jobs.table.status")}</th><th>{t("jobs.table.stage")}</th><th>{t("jobs.table.progress")}</th><th>{t("jobs.table.updated")}</th><th /></tr>
      </thead>
      <tbody>
        {jobs.map((job) => {
          const base = `/jobs/${encodeURIComponent(job.job_id)}`;
          const draft = job.overall === "draft" || job.overall === "starting";
          const waiting =
            job.overall === "paused" && job.current_stage === "approve_glossary" ? ["glossary", t("jobs.table.reviewGlossary")]
            : job.overall === "paused" && job.current_stage === "compile" ? ["review", t("jobs.table.openFinalReview")]
            : null;
          return (
            <tr key={job.job_id}>
              <td><Link className="mono" to={`${base}/${draft ? "config" : "progress"}`}>{job.job_id}</Link></td>
              <td>{job.source}</td>
              <td>{jobTypeLabel(job.job_type ?? "book")}</td>
              <td className="mono nowrap" title={job.direction || t("jobs.table.directionNotSet")}>{directionLabel(job.direction)}</td>
              <td><Chip kind={job.overall}>{job.overall}</Chip></td>
              <td>
                {draft ? <span className="meta">{t("jobs.table.notStarted")}</span> : job.current_stage ? stageLabel(job.current_stage) : "—"}
                {waiting && <div><Link to={`${base}/${waiting[0]}`}>{waiting[1]}</Link></div>}
              </td>
              <td style={{ minWidth: 140 }}>
                <Bar percent={(100 * job.completed) / job.total} />
                <span className="meta">{t("jobs.table.stagesDone", { completed: job.completed, total: job.total })}</span>
              </td>
              <td className="meta">{relativeTime(job.updated)}</td>
              <td>
                {job.downloadable && (
                  <a className="button small" href={outputUrl(job.job_id)} download title={job.job_type === "subtitles" ? t("jobs.downloadSubtitles") : t("jobs.downloadBook")}>{t("jobs.download")}</a>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

/** Create a draft job; with ``series`` the job also joins that series as its next volume. */
export function NewJobDialog({ setup, series, onClose }: {
  setup: Setup;
  series?: { series_id: string; name: string };
  onClose: () => void;
}) {
  const t = useT();
  const toast = useToast();
  const navigate = useNavigate();
  const [source, setSource] = useState("");
  const [book, setBook] = useState<BookInfo | null>(null);
  // A series shares one config across its volumes.
  const [configName, setConfigName] = useState(series ? `${series.series_id}.yaml` : "");
  const [suggestedId, setSuggestedId] = useState("");
  const seriesId = series?.series_id ?? "";
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
    api<{ config: string; job_id: string }>(`/api/jobs/suggest?source=${encodeURIComponent(source)}`)
      .then((s) => {
        if (!configTouched && !seriesId) setConfigName(s.config);
        setSuggestedId(seriesId ? `${seriesId}-${s.job_id}` : "");
      })
      // The names can still be typed by hand.
      .catch((e: Error) => toast("bad", t("jobs.new.noSuggestion", { error: e.message }), 0));
  }, [source, configTouched, seriesId, toast, t]);
  // Job ID: from the config name, or for a series book from the book (the config is shared).
  useEffect(() => {
    if (!jobTouched) setJobId(seriesId ? suggestedId : stem(configName));
  }, [configName, suggestedId, jobTouched, seriesId]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const upload = async (file: File | undefined) => {
    if (!file) return;
    if (!isSourceBook(file.name)) { toast("bad", t("jobs.new.chooseKinds", { kinds: sourceKinds() })); return; }
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

  const template = fileName(setup.template);
  const create = async () => {
    setBusy(true);
    try {
      const draft = await api<{ job_id: string; created_config: boolean }>("/api/jobs/new", {
        source, config: configName, job_id: jobId, series_id: series?.series_id ?? "",
      });
      toast("ok", !draft.created_config ? t("jobs.new.usingExisting", { config: configName })
        : template ? t("jobs.new.created", { config: configName, template })
        : t("jobs.new.createdEmpty", { config: configName }));
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
        <h2 id="new-job-title">{series ? t("jobs.new.titleInSeries", { series: series.name }) : t("jobs.new.title")}</h2>
        {series && (
          <p className="meta">{t("jobs.new.seriesNote")}</p>
        )}

        <div className="field">
          <label>{t("jobs.new.source")}</label>
          <input ref={picker} type="file" accept={SOURCE_ACCEPT} hidden onChange={(e) => { upload(e.target.files?.[0]); e.target.value = ""; }} />
          {book && !uploading ? (
            <div {...dropHandlers} className={over ? "drop-over" : ""}>
              <BookCard book={book} onChange={() => picker.current?.click()} />
            </div>
          ) : (
            <div {...dropHandlers} className={`dropzone ${over ? "over" : ""}`}>
              {uploading ? t("jobs.new.uploading") : rich("jobs.new.dropHint", {
                kinds: sourceKinds(),
                browse: (chunks) => <button type="button" className="small" onClick={() => picker.current?.click()}>{chunks}</button>,
              })}
            </div>
          )}
          <span className="hint">{t("jobs.new.uploadHint")}</span>
        </div>

        <div className="field">
          <label htmlFor="config-name">{t("jobs.new.configName")}</label>
          <input id="config-name" type="text" value={configName} spellCheck={false} placeholder={t("jobs.new.configPlaceholder")}
            onChange={(e) => { setConfigTouched(true); setConfigName(e.target.value); }} />
          <span className="hint">
            {!configName ? t("jobs.new.configPickSource")
              : !nameOk ? t("jobs.new.configBadName")
              : existing ? t(series ? "jobs.new.configExistsSeries" : "jobs.new.configExists", { config: configName, dir: setup.config_dir })
              : template ? t("jobs.new.configNew", { config: configName, dir: setup.config_dir, template })
              : t("jobs.new.configNewEmpty", { config: configName, dir: setup.config_dir })}
          </span>
        </div>

        <div className="field">
          <label htmlFor="job-id">{t("jobs.new.jobId")}</label>
          <input id="job-id" type="text" value={jobId} spellCheck={false}
            onChange={(e) => { setJobTouched(true); setJobId(e.target.value); }} />
          <span className="hint">
            {taken ? t("jobs.new.jobIdTaken") : series ? t("jobs.new.jobIdHintSeries") : t("jobs.new.jobIdHint")}
          </span>
        </div>

        <div className="row" style={{ justifyContent: "flex-end" }}>
          <button onClick={onClose}>{t("common.cancel")}</button>
          <button className="primary" disabled={busy || uploading || !source || !nameOk || !jobId || taken} onClick={create}>
            {t("jobs.new.create")}
          </button>
        </div>
      </section>
    </div>
  );
}

export function JobsPage() {
  const t = useT();
  const [setup, setSetup] = useState<Setup | null>(null);
  const [error, setError] = useState("");
  const [page, setPage] = useState(1);
  const [adding, setAdding] = useState(false);

  const refresh = useCallback(
    () => api<Setup>("/api/setup").then((next) => { setSetup(next); setError(""); }).catch((e: Error) => setError(e.message)),
    [],
  );
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 10000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const jobs = setup?.jobs ?? [];
  const pages = Math.max(1, Math.ceil(jobs.length / PAGE_SIZE));
  const current = Math.min(page, pages);
  const shown = jobs.slice((current - 1) * PAGE_SIZE, current * PAGE_SIZE);

  return (
    <Shell>
      <main className="page">
        <Card title={t("jobs.title")}>
          {error && <div className="banner bad">{error}</div>}
          {!setup && !error && <p className="meta">{t("common.loading")}</p>}
          {setup && !jobs.length && <p className="meta">{rich("jobs.empty", { runs: setup.runs, path: (chunks) => <span className="mono">{chunks}</span> })}</p>}
          {shown.length > 0 && <JobsTable jobs={shown} />}
          {jobs.length > 0 && (
            <div className="pager">
              <span className="meta">
                {t("jobs.range", { from: (current - 1) * PAGE_SIZE + 1, to: (current - 1) * PAGE_SIZE + shown.length, total: jobs.length })}
              </span>
              <Pager page={current} pages={pages} onPage={setPage} />
            </div>
          )}
          <button className="add-job" disabled={!setup} onClick={() => setAdding(true)}>{t("jobs.add")}</button>
        </Card>
      </main>
      {adding && setup && <NewJobDialog setup={setup} onClose={() => setAdding(false)} />}
    </Shell>
  );
}
