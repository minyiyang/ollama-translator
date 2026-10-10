import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { jobApi } from "../api";
import { useRerunDialog } from "../components/RerunDialog";
import { useLeftInSource } from "../components/LeftInSource";
import { Shell } from "../components/Shell";
import { StageTip } from "../components/StageTip";
import { NotStarted, useJob } from "../components/JobContext";
import { useToast } from "../components/Toast";
import { Bar, Card, Chip } from "../components/ui";
import { getLocale, rich, useT, type MessageKey, type Translate } from "../i18n";
import { statusLabel } from "../lib/enums";
import { count, duration, relativeTime, roughDuration, shortTimestamp } from "../lib/format";
import { stageMessage } from "../lib/serverText";
import { attentionFrom, lastStageAction, stageActions, stageLabel, type Stage, type WorkflowStatus } from "../lib/stages";

type Activity = {
  tasks: number;
  counter: [number, number] | null;
  segments: [number, number] | null;
  llm_calls: number;
  output_tokens: number;
  avg_call_seconds: number | null;
  first: string;
  last: string;
};
type Process = {
  label: string;
  running: boolean;
  exit_code: number | null;
  outcome: "running" | "completed" | "paused" | "cancelled" | "failed";
  started: string;
  output_tail: string;
};
type Snapshot = {
  status: WorkflowStatus;
  source: string;
  process: Process | null;
  progress: { log: string | null; line_count: number; lines: string[]; last_llm?: string; last_rate?: number; stages: Record<string, Activity> };
};

type Estimate = {
  elapsed_seconds: number;
  current: { stage: string; done: number; total: number; seconds_per_unit: number; remaining_seconds: number; basis: string; running: boolean } | null;
  pending: { stage: string; seconds: number }[];
  unknown_stages: string[];
  remaining_seconds: number | null;
  history_jobs: number;
  segments: number | null;
  excludes: string[];
  complete: boolean;
};

/** "About 1 h 20 min left": how the number was made is shown right under it. */
function EstimatePanel({ estimate, running }: { estimate: Estimate; running: boolean }) {
  const t = useT();
  const type = useJob().info?.job_type;
  if (estimate.complete) return null;
  const later = estimate.pending.reduce((sum, p) => sum + p.seconds, 0);
  const cur = estimate.current;
  return (
    <div className="estimate">
      <div className="estimate-main">
        {estimate.remaining_seconds !== null ? (
          rich(running ? "progress.estimate.left" : "progress.estimate.leftOnceResumed", { time: roughDuration(estimate.remaining_seconds), span: (chunks) => <span>{chunks}</span> })
        ) : (
          <span>{t("progress.estimate.notEnoughData")}</span>
        )}
      </div>
      <div className="meta">{t("progress.estimate.elapsed", { time: roughDuration(estimate.elapsed_seconds) })}</div>
      <ul className="estimate-parts">
        {cur && (
          <li>
            {rich("progress.estimate.current", { b: (chunks) => <b>{chunks}</b>, stage: stageLabel(cur.stage, type), time: roughDuration(cur.remaining_seconds), done: cur.done, total: cur.total, basis: cur.basis })}
          </li>
        )}
        {later > 0 && (
          <li>
            {estimate.segments
              ? t("progress.estimate.laterScaled", { time: roughDuration(later), jobs: estimate.history_jobs, segments: estimate.segments.toLocaleString(getLocale()) })
              : t("progress.estimate.later", { time: roughDuration(later), jobs: estimate.history_jobs })}
          </li>
        )}
        {estimate.unknown_stages.length > 0 && (
          <li>{t("progress.estimate.notEstimated", { stages: estimate.unknown_stages.map((stage) => stageLabel(stage, type)).join(", ") })}</li>
        )}
        {estimate.excludes.length > 0 && <li>{t("progress.estimate.excludes", { gates: estimate.excludes.map((g) => (g.includes(" ") ? g : stageLabel(g, type))).join("; ") })}</li>}
      </ul>
    </div>
  );
}

const ICONS: Record<string, string> = { completed: "✓", failed: "✕", paused: "❚❚", pending: "○", skipped: "–" };

function WorkCell({ stage, activity }: { stage: Stage; activity?: Activity }) {
  const t = useT();
  if (!activity) return null;
  const pair = activity.counter ?? activity.segments;
  const parts = [
    activity.tasks ? t("progress.work.tasks", { count: activity.tasks }) : "",
    pair ? t(activity.counter ? "progress.work.unit" : "progress.work.segment", { done: pair[0], total: pair[1] }) : "",
  ].filter(Boolean);
  const done = stage.status === "completed";
  return (
    <>
      <span className="meta">{parts.join(" · ")}</span>
      {(pair || done) && <Bar percent={done ? 100 : pair ? (100 * pair[0]) / pair[1] : 0} running={!done} />}
    </>
  );
}

const RESULT_STATE: Record<string, MessageKey> = {
  completed: "progress.result.completed",
  running: "progress.result.running",
  paused: "progress.result.paused",
  failed: "progress.result.failed",
  pending: "progress.result.pending",
};

/** Live one-paragraph result for the stage tooltip. */
function stageResult(t: Translate, stage: Stage, activity?: Activity): string {
  const parts: string[] = [RESULT_STATE[stage.status] ? t(RESULT_STATE[stage.status]) : stage.status];
  const pair = activity?.counter ?? activity?.segments;
  if (pair) parts.push(t(activity?.counter ? "progress.result.units" : "progress.result.segments", { done: pair[0], total: pair[1] }));
  if (activity?.llm_calls) {
    parts.push(activity.avg_call_seconds
      ? t("progress.result.callsAvg", { count: activity.llm_calls, seconds: activity.avg_call_seconds })
      : t("progress.result.calls", { count: activity.llm_calls }));
  }
  if (activity?.first && activity.last && stage.status !== "pending") parts.push(t("progress.result.thisSession", { time: duration(activity.first, activity.last) }));
  const summary = parts.join(" · ");
  return stage.message ? t("progress.result.summaryWithMessage", { summary, message: stageMessage(stage.message) }) : t("progress.result.summary", { summary });
}

/** Resume where the run stopped, or rerun a stage from scratch after a warning. */
function StageActionButtons({ stage, live, onLaunched }: { stage: Stage; live: boolean; onLaunched: () => void }) {
  const t = useT();
  const { jobId, info, refresh } = useJob();
  const type = info?.job_type;
  const confirmRerun = useRerunDialog();
  const toast = useToast();
  const [busy, setBusy] = useState(false);
  const actions = stageActions(stage);
  if (!actions.length) return null;

  const launch = async (path: string, body: object, done: string) => {
    setBusy(true);
    try {
      await jobApi(jobId, path, body);
      toast("ok", done);
      await refresh();
      onLaunched();
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setBusy(false);
    }
  };

  const rerun = async () => {
    if (await confirmRerun(stage.name)) {
      await launch("rerun", { stage: stage.name }, t("progress.rerun.started", { stage: stageLabel(stage.name, type) }));
    }
  };

  const blocked = live ? t("progress.action.blocked") : "";
  return (
    <span className="stage-actions">
      {actions.includes("resume") && (
        <button className="small" disabled={busy || live} title={blocked || t("progress.action.resumeHint")}
          onClick={() => launch("resume", {}, t("progress.action.resumed"))}>{t("progress.action.resume")}</button>
      )}
      {actions.includes("rerun") && (
        <button className="small" disabled={busy || live} title={blocked || t("progress.action.rerunHint")}
          onClick={rerun}>{actions.includes("resume") ? t("progress.action.rerun") : t("progress.action.rerunFromHere")}</button>
      )}
    </span>
  );
}

function lineClass(line: string) {
  if (line.includes("[llm]")) return "l-llm";
  if (/\] \[(running|completed|paused|failed)\]/.test(line)) return "l-stage";
  if (/failed|error/i.test(line)) return "l-bad";
  return "";
}

/** When a stage last changed status, and to what: "done · Sep 18 12:08". */
function LastChange({ stage }: { stage: Stage }) {
  const action = lastStageAction(stage);
  if (!action || !stage.updated_at) return null;
  const at = new Date(stage.updated_at);
  return (
    <span title={`${at.toLocaleString(getLocale())} (${relativeTime(stage.updated_at)})`}>
      <span className="meta">{action}</span> <span className="mono">{shortTimestamp(stage.updated_at)}</span>
    </span>
  );
}

export function ProgressPage() {
  const t = useT();
  const { jobId, info } = useJob();
  const started = info?.kind === "job";
  const [data, setData] = useState<Snapshot | null>(null);
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [error, setError] = useState("");
  const [lines, setLines] = useState<string[]>([]);
  const [hideHeartbeat, setHideHeartbeat] = useState(true);
  const [hideSegments, setHideSegments] = useState(true);
  const [follow, setFollow] = useState(true);
  const cursor = useRef({ log: "", count: 0 });
  const timer = useRef<number | undefined>(undefined);
  // Bumped when the page leaves or changes job, so a request still in flight is dropped.
  const visit = useRef(0);
  const logBox = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    window.clearTimeout(timer.current);
    const mine = visit.current;
    let live = false;
    try {
      const { log, count: after } = cursor.current;
      const next = await jobApi<Snapshot>(jobId, `progress?after=${after}&log=${encodeURIComponent(log)}`);
      if (mine !== visit.current) return;
      const p = next.progress;
      const switched = (p.log ?? "") !== log;
      if (switched || p.lines.length) {
        setLines((old) => (switched ? p.lines : [...old, ...p.lines]).slice(-2000));
        cursor.current = { log: p.log ?? "", count: p.line_count };
      }
      setData(next);
      setError("");
      jobApi<Estimate>(jobId, "estimate").then(setEstimate).catch(() => setEstimate(null));
      live = next.status.overall === "running" || !!next.process?.running;
    } catch (e) {
      if (mine !== visit.current) return;
      setError((e as Error).message);
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(poll, live ? 2000 : 8000);
  }, [jobId]);

  useEffect(() => {
    if (!started) return;
    cursor.current = { log: "", count: 0 };
    setLines([]);
    poll();
    return () => { visit.current += 1; window.clearTimeout(timer.current); };
  }, [poll, started]);

  useEffect(() => {
    if (follow && logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight;
  }, [lines, follow, hideHeartbeat, hideSegments]);

  const status = data?.status;
  const attention = attentionFrom(status);
  const stages = status?.stages ?? [];
  const done = stages.filter((s) => s.status === "completed").length;
  // What the title stage left in the source language, once it has run: its row says so, and where to fix it.
  const titleDone = stages.some((s) => s.name === "translate_title" && s.status === "completed");
  const leftInSource = useLeftInSource(jobId, started && titleDone);
  const current = stages.find((s) => ["running", "paused", "failed"].includes(s.status)) ?? stages.find((s) => s.status === "pending");
  const proc = data?.process;
  const live = status?.overall === "running" || !!proc?.running;
  const shown = lines.filter((l) => !(hideHeartbeat && l.includes("streaming —")) && !(hideSegments && l.includes("] [segment] "))).slice(-600);
  const base = `/jobs/${encodeURIComponent(jobId)}`;
  const stageEstimate = (name: string, stageStatus: string) => {
    if (!estimate || stageStatus === "completed") return "";
    if (estimate.current?.stage === name) return t("progress.stageEstimate.left", { time: roughDuration(estimate.current.remaining_seconds) });
    const planned = estimate.pending.find((p) => p.stage === name);
    if (planned) return planned.seconds < 1 ? "" : `~${roughDuration(planned.seconds)}`;
    if (estimate.excludes.includes(name)) return t("progress.stageEstimate.yourReview");
    return estimate.unknown_stages.includes(name) ? "?" : "";
  };

  return (
    <Shell jobId={jobId}>
      <main className="page">
        {info?.kind === "draft" && <NotStarted what="progress" />}
        {error && <div className="banner bad">{error}</div>}
        {started && !data && !error && <p className="meta">{t("common.loading")}</p>}
        {data && status && (
          <>
            <section className="card summary">
              <div>
                <Chip kind={status.overall}>{statusLabel(status.overall)}</Chip>
                <span className="meta" style={{ marginInlineStart: 6 }}>{data.source}</span>
                <div className="big">{current ? stageLabel(current.name, info?.job_type) : t("progress.summary.allComplete")}</div>
                {current?.message && <div className="meta">{stageMessage(current.message)}</div>}
                <Bar percent={(100 * done) / stages.length} running={status.overall !== "complete"} />
                <span className="meta">{t("progress.summary.stagesComplete", { done, total: stages.length })}</span>
                {proc && (
                  <div className="meta" style={{ marginTop: 6 }}>
                    {rich("progress.summary.process", { b: (chunks) => <b>{chunks}</b>, label: proc.label, started: proc.started, outcome: proc.outcome, code: String(proc.exit_code) })}
                  </div>
                )}
                {proc?.outcome === "failed" && (
                  <div className="banner bad" style={{ marginTop: 10 }}>{t("progress.summary.commandFailed", { label: proc.label, code: proc.exit_code })}<pre>{proc.output_tail}</pre></div>
                )}
              </div>
              <div className="summary-side">
                {estimate && <EstimatePanel estimate={estimate} running={live} />}
                <div className="row" style={{ margin: 0, justifyContent: "flex-end" }}>
                {attention.glossary ? (
                  <Link to={`${base}/glossary`}><button className="primary">{t("progress.summary.reviewGlossary")}</button></Link>
                ) : attention.review ? (
                  <Link to={`${base}/review`}><button className="primary">{t("progress.summary.openFinalReview")}</button></Link>
                ) : null}
                </div>
              </div>
            </section>

            {live && data.progress.last_llm && (
              <Card title={t("progress.now.title")}>
                <div className="now">
                  {data.progress.last_llm}
                  {data.progress.last_rate ? `\n${t("progress.now.lastRate", { rate: data.progress.last_rate })}` : ""}
                </div>
              </Card>
            )}

            <Card title={t("progress.pipeline.title")}>
              <table className="grid stages">
                <thead>
                  <tr><th /><th>{t("progress.pipeline.stage")}</th><th>{t("progress.pipeline.work")}</th><th className="num">{t("progress.pipeline.llmCalls")}</th><th className="num">{t("progress.pipeline.outputTokens")}</th><th className="num">{t("progress.pipeline.time")}</th><th className="num">{t("progress.pipeline.estimate")}</th><th className="num">{t("progress.pipeline.attempts")}</th><th>{t("progress.pipeline.lastChange")}</th><th /></tr>
                </thead>
                <tbody>
                  {stages.map((stage) => {
                    const activity = data.progress.stages[stage.name];
                    return (
                      <tr key={stage.name} className={stage.status}>
                        <td className={`icon ${stage.status}`}>{stage.status === "running" ? <span>◐</span> : ICONS[stage.status] ?? "○"}</td>
                        <td>
                          <b>{stageLabel(stage.name, info?.job_type)}</b> <StageTip stage={stage.name} jobType={info?.job_type} result={stageResult(t, stage, activity)} />{" "}
                          <span className="meta mono">{stage.name}</span>
                          {stage.message && <div className="msg">{stageMessage(stage.message)}</div>}
                          {stage.name === "translate_title" && leftInSource.length > 0 && (
                            <div className="msg">
                              <Link to={`${base}/text?view=untranslated`}>
                                {t("progress.pipeline.leftInSource", { count: leftInSource.length })}
                              </Link>
                            </div>
                          )}
                        </td>
                        <td className="work"><WorkCell stage={stage} activity={activity} /></td>
                        <td className="num">
                          {count(activity?.llm_calls)}
                          {activity?.avg_call_seconds ? <div className="meta">{t("progress.pipeline.avgCall", { seconds: activity.avg_call_seconds })}</div> : null}
                        </td>
                        <td className="num">{count(activity?.output_tokens)}</td>
                        <td className="num meta">{activity ? duration(activity.first, activity.last) : ""}</td>
                        <td className="num meta">{stageEstimate(stage.name, stage.status)}</td>
                        <td className="num meta">{stage.attempts || ""}</td>
                        <td className="last-change"><LastChange stage={stage} /></td>
                        <td className="num"><StageActionButtons stage={stage} live={live} onLaunched={poll} /></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>

            <section className="card">
              <div className="row" style={{ margin: "0 0 10px", justifyContent: "space-between" }}>
                <h2 style={{ margin: 0 }}>{t("progress.log.title")} <span className="meta mono" style={{ textTransform: "none" }}>{data.progress.log}</span></h2>
                <span className="row" style={{ margin: 0 }}>
                  <label className="meta"><input type="checkbox" checked={hideHeartbeat} onChange={(e) => setHideHeartbeat(e.target.checked)} /> {t("progress.log.hideHeartbeats")}</label>
                  <label className="meta"><input type="checkbox" checked={hideSegments} onChange={(e) => setHideSegments(e.target.checked)} /> {t("progress.log.hideSegments")}</label>
                  <label className="meta"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> {t("progress.log.follow")}</label>
                </span>
              </div>
              <div className="log logview" ref={logBox}>
                {shown.length ? shown.map((line, i) => <div key={i} className={lineClass(line)}>{line}</div>) : <span className="meta">{t("progress.log.empty")}</span>}
              </div>
            </section>
          </>
        )}
      </main>
    </Shell>
  );
}
