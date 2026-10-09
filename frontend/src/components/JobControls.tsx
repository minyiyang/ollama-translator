import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { jobApi } from "../api";
import { useT, type MessageKey } from "../i18n";
import { statusLabel } from "../lib/enums";
import { attentionFrom } from "../lib/stages";
import { useConfirm } from "./Dialog";
import { DownloadControl } from "./DownloadControl";
import { useJob } from "./JobContext";
import { useToast } from "./Toast";
import { Chip } from "./ui";

const TIPS = {
  start: "jobs.controls.startTip",
  startBlocked: "jobs.controls.startBlockedTip",
  pause: "jobs.controls.pauseTip",
  pausing: "jobs.controls.pausingTip",
  stop: "jobs.controls.stopTip",
  stopBlocked: "jobs.controls.stopBlockedTip",
  resume: "jobs.controls.resumeTip",
} as const satisfies Record<string, MessageKey>;

/** Start / Pause / Stop / Resume for the current job, shown in the header on every job tab. */
export function JobControls() {
  const t = useT();
  const { jobId, info, refresh } = useJob();
  const toast = useToast();
  const confirm = useConfirm();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  if (!info) return null;

  type Ask = { title: string; body: string; label: string };
  const act = async (path: string, done: string, ask?: Ask) => {
    if (ask && !(await confirm(ask.title, ask.body, ask.label, true))) return;
    setBusy(true);
    try {
      await jobApi(jobId, path, {});
      toast("ok", done);
      await refresh();
      if (path === "start") navigate(`/jobs/${encodeURIComponent(jobId)}/progress`);
      if (path === "discard") navigate("/");
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const attention = attentionFrom({ job_id: jobId, overall: info.overall, stages: info.stages, configuration: { source_path: "" } });
  const waiting = attention.glossary || attention.review;
  const status = info.pause_requested ? "pausing" : info.overall;

  return (
    <>
      <Chip kind={status}>{statusLabel(status)}</Chip>
      {info.kind === "draft" && !info.running && (
        <>
          <button onClick={() => act("discard", t("jobs.controls.discarded"), { title: t("jobs.controls.discardTitle"), body: t("jobs.controls.discardBody"), label: t("jobs.controls.discard") })} disabled={busy}>{t("jobs.controls.discard")}</button>
          <span title={t(info.validated ? TIPS.start : TIPS.startBlocked)}>
            <button className="primary" disabled={busy || !info.validated} onClick={() => act("start", t("jobs.controls.started"))}>
              {t("jobs.controls.start")}
            </button>
          </span>
        </>
      )}
      {info.running && info.kind === "job" && (
        <span title={t(info.pause_requested ? TIPS.pausing : TIPS.pause)}>
          <button disabled={busy || info.pause_requested} onClick={() => act("pause", t("jobs.controls.pauseRequested"))}>
            {info.pause_requested ? t("jobs.controls.pausing") : t("jobs.controls.pause")}
          </button>
        </span>
      )}
      {info.running && (
        <span title={t(info.can_stop ? TIPS.stop : TIPS.stopBlocked)}>
          <button disabled={busy || !info.can_stop}
            onClick={() => act("stop", t("jobs.controls.stopped"), { title: t("jobs.controls.stopTitle"), body: t("jobs.controls.stopBody"), label: t("jobs.controls.stopConfirm") })}>
            {t("jobs.controls.stop")}
          </button>
        </span>
      )}
      {info.downloadable && <DownloadControl jobId={jobId} output={info} />}
      {info.kind === "job" && !info.running && !waiting && info.overall !== "complete" && (
        <span title={t(TIPS.resume)}>
          <button className="primary" disabled={busy} onClick={() => act("resume", t("jobs.controls.resumed"))}>{t("jobs.controls.resume")}</button>
        </span>
      )}
    </>
  );
}
