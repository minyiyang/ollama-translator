import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { jobApi } from "../api";
import { attentionFrom } from "../lib/stages";
import { useConfirm } from "./Dialog";
import { useJob } from "./JobContext";
import { useToast } from "./Toast";
import { Chip } from "./ui";

const TIPS = {
  start: "Starts the pipeline with the validated configuration.",
  startBlocked: "Validate the configuration on the Config tab first.",
  pause: "Lets the current LLM call finish, then pauses. Resume continues from there; no finished work is lost.",
  pausing: "Waiting for the current LLM call to finish.",
  stop: "Stops immediately. The LLM call in progress is lost and redone on resume.",
  stopBlocked: "Only runs started from this dashboard can be stopped here; use Pause.",
  resume: "Continues from the last checkpoint.",
};

/** Start / Pause / Stop / Resume for the current job, shown in the header on every job tab. */
export function JobControls() {
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
      <Chip kind={status}>{status}</Chip>
      {info.kind === "draft" && !info.running && (
        <>
          <button onClick={() => act("discard", "Draft discarded.", { title: "Discard this job?", body: "The draft job is removed. Its config file is kept.", label: "Discard" })} disabled={busy}>Discard</button>
          <span title={info.validated ? TIPS.start : TIPS.startBlocked}>
            <button className="primary" disabled={busy || !info.validated} onClick={() => act("start", "Translation started.")}>
              Start translation
            </button>
          </span>
        </>
      )}
      {info.running && info.kind === "job" && (
        <span title={info.pause_requested ? TIPS.pausing : TIPS.pause}>
          <button disabled={busy || info.pause_requested} onClick={() => act("pause", "Pause requested: the run stops after the current LLM call.")}>
            {info.pause_requested ? "Pausing…" : "❚❚ Pause"}
          </button>
        </span>
      )}
      {info.running && (
        <span title={info.can_stop ? TIPS.stop : TIPS.stopBlocked}>
          <button disabled={busy || !info.can_stop}
            onClick={() => act("stop", "Stopped.", { title: "Stop the run now?", body: "The LLM call in progress is discarded and redone when you resume.", label: "Stop" })}>
            ■ Stop
          </button>
        </span>
      )}
      {info.kind === "job" && !info.running && !waiting && info.overall !== "complete" && (
        <span title={TIPS.resume}>
          <button className="primary" disabled={busy} onClick={() => act("resume", "Resumed.")}>▶ Resume</button>
        </span>
      )}
    </>
  );
}
