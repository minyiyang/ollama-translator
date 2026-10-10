import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, jobApi } from "../api";
import { BookCard, type BookInfo } from "../components/BookCard";
import { CheckResult, type Check } from "../components/CheckResult";
import { ConfigChangeList, ConfigHistory, type ConfigPreview, type SavedChange } from "../components/ConfigChanges";
import { ConfigEditor } from "../components/ConfigEditor";
import { useJob } from "../components/JobContext";
import { useRerunDialog } from "../components/RerunDialog";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Card } from "../components/ui";
import { rich, useT } from "../i18n";
import { stageLabel } from "../lib/stages";

type JobConfig = {
  editable: boolean;
  name: string;
  text: string;
  validated: boolean;
  /** A started job: whether its config can be unlocked now (not while it runs). */
  unlockable?: boolean;
  /** A started job: the settings that stay locked, each with its reason. */
  locked?: Record<string, string>;
  /** A started job: the changes saved to its config before. */
  changes?: SavedChange[];
};

/**
 * First job tab: edit and validate a draft's config, or view a started job's
 * captured config. A started job's config is unlocked to change it: the page
 * then says what each change does to the pipeline, and saves with a rerun of
 * the earliest finished stage affected (docs/OUTPUT_AND_CONFIG_UX.md, 4).
 */
export function ConfigPage() {
  const t = useT();
  const { jobId, info, refresh } = useJob();
  const toast = useToast();
  const navigate = useNavigate();
  const confirmRerun = useRerunDialog();
  const [config, setConfig] = useState<JobConfig | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [check, setCheck] = useState<Check | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [book, setBook] = useState<BookInfo | null>(null);
  // A started job's config, unlocked for editing, and what the edited text would change.
  const [unlocked, setUnlocked] = useState(false);
  const [preview, setPreview] = useState<ConfigPreview | null>(null);
  const previewSeq = useRef(0);
  const kind = info?.kind;
  const sourcePath = info?.source_path;
  const running = !!info?.running;

  useEffect(() => {
    if (!sourcePath) return;
    api<BookInfo>(`/api/book?path=${encodeURIComponent(sourcePath)}`).then(setBook).catch(() => setBook(null));
  }, [sourcePath]);

  const load = () =>
    jobApi<JobConfig>(jobId, "config")
      .then((c) => { setConfig(c); setText(c.text); setSaved(c.text); })
      .catch((e: Error) => setError(e.message));
  useEffect(() => {
    if (!kind) return;
    load();
    // Loaded once a job, and again when a draft becomes a started job.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, kind]);

  const dirty = text !== saved;
  const editable = !!config?.editable;
  const started = !!config && !editable;

  // A job that starts running while its config is unlocked is locked again: nothing is saved under a run.
  useEffect(() => {
    if (running && unlocked) { setUnlocked(false); setText(saved); setPreview(null); }
  }, [running, unlocked, saved]);

  // What the edited text would change, asked of the server a moment after the last keystroke.
  useEffect(() => {
    if (!unlocked) return;
    if (!dirty) { setPreview(null); return; }
    const seq = ++previewSeq.current;
    const handle = window.setTimeout(() => {
      jobApi<ConfigPreview>(jobId, "config/preview", { text })
        .then((next) => { if (seq === previewSeq.current) setPreview(next); })
        .catch((e: Error) => {
          if (seq === previewSeq.current) setPreview({ errors: [{ path: "", message: e.message }], changes: [], locked: [], rerun_stage: "" });
        });
    }, 400);
    return () => window.clearTimeout(handle);
  }, [jobId, text, dirty, unlocked]);

  // Validating is also how a draft's config is saved: a file is never started unchecked.
  const validate = async () => {
    setBusy(true);
    try {
      const result = await jobApi<Check & { validated: boolean }>(jobId, "validate", { text });
      setSaved(text);
      setCheck(result);
      await refresh();
    } catch (e) {
      setCheck(null);
      toast("bad", <>{t("config.page.notSaved")}<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setBusy(false);
    }
  };

  const lock = () => { setUnlocked(false); setText(saved); setPreview(null); };

  /** Save a started job's changed config; with `rerun`, then rerun the job from the stage the change first affects. */
  const save = async (rerun: string) => {
    // Asked first: declining the rerun saves nothing.
    if (rerun && !(await confirmRerun(rerun))) return;
    setBusy(true);
    try {
      await jobApi(jobId, "config", { text });
      setUnlocked(false);
      setPreview(null);
      await load();
      if (rerun) {
        await jobApi(jobId, "rerun", { stage: rerun });
        toast("ok", t("config.page.savedAndRerun", { stage: stageLabel(rerun, info?.job_type) }));
        await refresh();
        navigate(`/jobs/${encodeURIComponent(jobId)}/progress`);
      } else {
        toast("ok", t("config.page.saved"));
        await refresh();
      }
    } catch (e) {
      toast("bad", <>{t("config.page.notSaved")}<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setBusy(false);
    }
  };

  const status = !editable ? null
    : dirty ? <div className="banner warn">{t("config.page.unsaved")}</div>
    : info?.validated ? <div className="banner ok">{rich("config.page.validated", { b: (chunks) => <b>{chunks}</b> })}</div>
    : <div className="banner info">{rich("config.page.notValidated", { b: (chunks) => <b>{chunks}</b> })}</div>;

  const savable = !!preview && !preview.errors.length && !preview.locked.length && preview.changes.length > 0;

  return (
    <Shell jobId={jobId}>
      <main className="page">
        {error && <div className="banner bad">{error}</div>}
        {info?.kind === "draft" && info.process?.outcome === "failed" && (
          <div className="banner bad">{t("config.page.startFailed")}<pre>{info.process.output_tail}</pre></div>
        )}
        {!config && !error && <p className="meta">{t("common.loading")}</p>}
        {config && info && (
          <div className="jobs-layout">
            <Card title={editable ? t("config.page.title") : unlocked ? t("config.page.titleUnlocked") : t("config.page.titleReadOnly")}>
              <div style={{ marginBottom: 14 }}>
                {book ? (
                  <BookCard book={book} compact extra={[[t("config.page.bookConfig"), config.name]]} />
                ) : (
                  <div className="row" style={{ marginTop: 0, gap: 16 }}>
                    <span><span className="meta">{t("config.page.source")} </span><span className="mono">{info.source_path ?? info.source}</span></span>
                    <span><span className="meta">{t("config.page.configFile")} </span><span className="mono">{config.name}</span></span>
                  </div>
                )}
              </div>
              {started && !unlocked && (
                <div className="row" style={{ marginTop: 0 }}>
                  <p className="meta" style={{ flex: 1, minWidth: 240, margin: 0 }}>
                    {t(running ? "config.page.lockedRunning" : "config.page.locked")}
                  </p>
                  <span title={running ? t("config.page.lockedRunning") : t("config.page.unlockHint")}>
                    <button onClick={() => setUnlocked(true)} disabled={running || config.unlockable === false}>{t("config.page.unlock")}</button>
                  </span>
                </div>
              )}
              {started && unlocked && <div className="banner info">{t("config.page.unlocked")}</div>}
              <ConfigEditor
                text={text}
                onTextChange={setText}
                hasComments={/(^|\n)\s*#/.test(saved)}
                readOnly={!editable && !unlocked}
                jobType={info.job_type}
                locked={started ? config.locked : undefined}
              />
            </Card>
            {editable && (
              <div className="sticky-side">
                <Card title={t("config.page.validate")}>
                  {status}
                  <div className="row" style={{ marginTop: 0 }}>
                    <button className="primary" onClick={validate} disabled={busy}>{busy ? t("config.page.validating") : dirty ? t("config.page.saveAndValidate") : t("config.page.validate")}</button>
                  </div>
                  <p className="meta">
                    {rich("config.page.validateHelp", { name: <span className="mono">{config.name}</span> })}
                  </p>
                  {check && <CheckResult check={check} jobType={info.job_type} />}
                </Card>
              </div>
            )}
            {started && (unlocked || !!config.changes?.length) && (
              <div className="sticky-side">
                <Card title={t("config.changes.title")}>
                  {unlocked && (
                    <>
                      {!dirty && <p className="meta">{t("config.changes.none")}</p>}
                      {dirty && !preview && <p className="meta">{t("config.changes.checking")}</p>}
                      {dirty && preview && <ConfigChangeList preview={preview} jobType={info.job_type} />}
                      {dirty && preview && preview.locked.length > 0 && (
                        <div className="banner bad">{t("config.changes.lockedChanged")}</div>
                      )}
                      {savable && (
                        <p className="meta">
                          {preview!.rerun_stage
                            ? t("config.changes.needsRerun", { stage: stageLabel(preview!.rerun_stage, info.job_type) })
                            : t("config.changes.noRerun")}
                        </p>
                      )}
                      <div className="row">
                        {savable && preview!.rerun_stage && (
                          <button className="primary" disabled={busy} onClick={() => save(preview!.rerun_stage)}>
                            {t("config.changes.saveAndRerun", { stage: stageLabel(preview!.rerun_stage, info.job_type) })}
                          </button>
                        )}
                        {savable && !preview!.rerun_stage && (
                          <button className="primary" disabled={busy} onClick={() => save("")}>{t("config.changes.save")}</button>
                        )}
                        <button disabled={busy} onClick={lock}>{t(dirty ? "config.changes.discard" : "config.changes.lock")}</button>
                      </div>
                    </>
                  )}
                  <ConfigHistory history={config.changes ?? []} />
                </Card>
              </div>
            )}
          </div>
        )}
      </main>
    </Shell>
  );
}
