import { useEffect, useState } from "react";
import { api, jobApi } from "../api";
import { BookCard, type BookInfo } from "../components/BookCard";
import { CheckResult, type Check } from "../components/CheckResult";
import { ConfigEditor } from "../components/ConfigEditor";
import { useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Card } from "../components/ui";

type JobConfig = { editable: boolean; name: string; text: string; validated: boolean };

/** First job tab: edit and validate a draft's config, or view a started job's captured config. */
export function ConfigPage() {
  const { jobId, info, refresh } = useJob();
  const toast = useToast();
  const [config, setConfig] = useState<JobConfig | null>(null);
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [check, setCheck] = useState<Check | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [book, setBook] = useState<BookInfo | null>(null);
  const kind = info?.kind;
  const sourcePath = info?.source_path;

  useEffect(() => {
    if (!sourcePath) return;
    api<BookInfo>(`/api/book?path=${encodeURIComponent(sourcePath)}`).then(setBook).catch(() => setBook(null));
  }, [sourcePath]);

  useEffect(() => {
    if (!kind) return;
    jobApi<JobConfig>(jobId, "config")
      .then((c) => { setConfig(c); setText(c.text); setSaved(c.text); })
      .catch((e: Error) => setError(e.message));
  }, [jobId, kind]);

  const dirty = text !== saved;
  const editable = !!config?.editable;

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
      toast("bad", <>Not saved:<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setBusy(false);
    }
  };

  const status = !editable ? null
    : dirty ? <div className="banner warn">Unsaved changes. Validate saves them; until then the job would start with the last validated file.</div>
    : info?.validated ? <div className="banner ok">Validated. <b>Start translation</b> at the top is ready.</div>
    : <div className="banner info">Not validated yet. Validate to enable <b>Start translation</b>.</div>;

  return (
    <Shell jobId={jobId}>
      <main className="page">
        {error && <div className="banner bad">{error}</div>}
        {info?.kind === "draft" && info.process?.outcome === "failed" && (
          <div className="banner bad">The last start failed before the job workspace was created:<pre>{info.process.output_tail}</pre></div>
        )}
        {!config && !error && <p className="meta">Loading…</p>}
        {config && info && (
          <div className="jobs-layout">
            <Card title={editable ? "Configuration" : "Configuration used by this run"}>
              <div style={{ marginBottom: 14 }}>
                {book ? (
                  <BookCard book={book} compact extra={[["Config", config.name]]} />
                ) : (
                  <div className="row" style={{ marginTop: 0, gap: 16 }}>
                    <span><span className="meta">Source </span><span className="mono">{info.source_path ?? info.source}</span></span>
                    <span><span className="meta">Config file </span><span className="mono">{config.name}</span></span>
                  </div>
                )}
              </div>
              {!editable && (
                <p className="meta">The run captured this configuration when it started; it is read-only. To change it, create a new job.</p>
              )}
              <ConfigEditor text={text} onTextChange={setText} hasComments={/(^|\n)\s*#/.test(saved)} readOnly={!editable} />
            </Card>
            {editable && (
              <div className="sticky-side">
                <Card title="Validate">
                  {status}
                  <div className="row" style={{ marginTop: 0 }}>
                    <button className="primary" onClick={validate} disabled={busy}>{busy ? "Validating…" : dirty ? "Save & validate" : "Validate"}</button>
                  </div>
                  <p className="meta">
                    Saves <span className="mono">{config.name}</span>, checks every setting, dry-runs the job, and asks Ollama which models are installed.
                    Editing the config after validating requires validating again.
                  </p>
                  {check && <CheckResult check={check} />}
                </Card>
              </div>
            )}
          </div>
        )}
      </main>
    </Shell>
  );
}
