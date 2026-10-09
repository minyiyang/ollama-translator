import { useEffect, useState } from "react";
import { api, jobApi } from "../api";
import { BookCard, type BookInfo } from "../components/BookCard";
import { CheckResult, type Check } from "../components/CheckResult";
import { ConfigEditor } from "../components/ConfigEditor";
import { useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { useToast } from "../components/Toast";
import { Card } from "../components/ui";
import { rich, useT } from "../i18n";

type JobConfig = { editable: boolean; name: string; text: string; validated: boolean };

/** First job tab: edit and validate a draft's config, or view a started job's captured config. */
export function ConfigPage() {
  const t = useT();
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
      toast("bad", <>{t("config.page.notSaved")}<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setBusy(false);
    }
  };

  const status = !editable ? null
    : dirty ? <div className="banner warn">{t("config.page.unsaved")}</div>
    : info?.validated ? <div className="banner ok">{rich("config.page.validated", { b: (chunks) => <b>{chunks}</b> })}</div>
    : <div className="banner info">{rich("config.page.notValidated", { b: (chunks) => <b>{chunks}</b> })}</div>;

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
            <Card title={editable ? t("config.page.title") : t("config.page.titleReadOnly")}>
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
              {!editable && (
                <p className="meta">{t("config.page.readOnly")}</p>
              )}
              <ConfigEditor text={text} onTextChange={setText} hasComments={/(^|\n)\s*#/.test(saved)} readOnly={!editable} jobType={info.job_type} />
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
          </div>
        )}
      </main>
    </Shell>
  );
}
