import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { jobApi } from "../api";
import { Shell } from "../components/Shell";
import { StageTip } from "../components/StageTip";
import { NotStarted, useJob } from "../components/JobContext";
import { Bar, Card, Chip } from "../components/ui";
import { count, duration, roughDuration } from "../lib/format";
import { attentionFrom, stageLabel, type Stage, type WorkflowStatus } from "../lib/stages";

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
const OUTCOME_TEXT: Record<Process["outcome"], string> = {
  running: "running",
  completed: "finished",
  paused: "paused, waiting for you",
  cancelled: "stopped",
  failed: "failed",
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
  if (estimate.complete) return null;
  const later = estimate.pending.reduce((sum, p) => sum + p.seconds, 0);
  const cur = estimate.current;
  return (
    <div className="estimate">
      <div className="estimate-main">
        {estimate.remaining_seconds !== null ? (
          <>≈ {roughDuration(estimate.remaining_seconds)} <span>left{running ? "" : " once resumed"}</span></>
        ) : (
          <span>Not enough data to estimate yet</span>
        )}
      </div>
      <div className="meta">{roughDuration(estimate.elapsed_seconds)} of pipeline time so far</div>
      <ul className="estimate-parts">
        {cur && (
          <li>
            <b>{stageLabel(cur.stage)}</b>: ~{roughDuration(cur.remaining_seconds)} ({cur.done}/{cur.total} units done; {cur.basis})
          </li>
        )}
        {later > 0 && (
          <li>
            Later stages: ~{roughDuration(later)}, from {estimate.history_jobs} previous job{estimate.history_jobs === 1 ? "" : "s"}
            {estimate.segments ? ` scaled to ${estimate.segments.toLocaleString()} segments` : ""}
          </li>
        )}
        {estimate.unknown_stages.length > 0 && (
          <li>Not estimated (no history yet): {estimate.unknown_stages.map(stageLabel).join(", ")}</li>
        )}
        {estimate.excludes.length > 0 && <li>Excludes waiting for you: {estimate.excludes.map((g) => (g.includes(" ") ? g : stageLabel(g))).join("; ")}</li>}
      </ul>
    </div>
  );
}

const ICONS: Record<string, string> = { completed: "✓", failed: "✕", paused: "❚❚", pending: "○", skipped: "–" };

function WorkCell({ stage, activity }: { stage: Stage; activity?: Activity }) {
  if (!activity) return null;
  const pair = activity.counter ?? activity.segments;
  const parts = [
    activity.tasks ? `${activity.tasks} LLM task${activity.tasks === 1 ? "" : "s"}` : "",
    pair ? `${activity.counter ? "unit" : "segment"} ${pair[0]}/${pair[1]}` : "",
  ].filter(Boolean);
  const done = stage.status === "completed";
  return (
    <>
      <span className="meta">{parts.join(" · ")}</span>
      {(pair || done) && <Bar percent={done ? 100 : pair ? (100 * pair[0]) / pair[1] : 0} running={!done} />}
    </>
  );
}

/** Live one-paragraph result for the stage tooltip. */
function stageResult(stage: Stage, activity?: Activity): string {
  const state = { completed: "Completed", running: "Running", paused: "Paused", failed: "Failed", pending: "Not started yet" }[stage.status] ?? stage.status;
  const parts: string[] = [state];
  const pair = activity?.counter ?? activity?.segments;
  if (pair) parts.push(`${pair[0]}/${pair[1]} ${activity?.counter ? "units" : "segments"}`);
  if (activity?.llm_calls) {
    parts.push(`${activity.llm_calls} LLM call${activity.llm_calls === 1 ? "" : "s"}${activity.avg_call_seconds ? `, avg ${activity.avg_call_seconds}s` : ""}`);
  }
  if (activity?.first && activity.last && stage.status !== "pending") parts.push(`${duration(activity.first, activity.last)} this session`);
  return parts.join(" · ") + (stage.message ? `. ${stage.message}` : ".");
}

function lineClass(line: string) {
  if (line.includes("[llm]")) return "l-llm";
  if (/\] \[(running|completed|paused|failed)\]/.test(line)) return "l-stage";
  if (/failed|error/i.test(line)) return "l-bad";
  return "";
}

export function ProgressPage() {
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
  const logBox = useRef<HTMLDivElement>(null);

  const poll = useCallback(async () => {
    window.clearTimeout(timer.current);
    let live = false;
    try {
      const { log, count: after } = cursor.current;
      const next = await jobApi<Snapshot>(jobId, `progress?after=${after}&log=${encodeURIComponent(log)}`);
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
      setError((e as Error).message);
    }
    timer.current = window.setTimeout(poll, live ? 2000 : 8000);
  }, [jobId]);

  useEffect(() => {
    if (!started) return;
    cursor.current = { log: "", count: 0 };
    setLines([]);
    poll();
    return () => window.clearTimeout(timer.current);
  }, [poll, started]);

  useEffect(() => {
    if (follow && logBox.current) logBox.current.scrollTop = logBox.current.scrollHeight;
  }, [lines, follow, hideHeartbeat, hideSegments]);

  const status = data?.status;
  const attention = attentionFrom(status);
  const stages = status?.stages ?? [];
  const done = stages.filter((s) => s.status === "completed").length;
  const current = stages.find((s) => ["running", "paused", "failed"].includes(s.status)) ?? stages.find((s) => s.status === "pending");
  const proc = data?.process;
  const live = status?.overall === "running" || !!proc?.running;
  const shown = lines.filter((l) => !(hideHeartbeat && l.includes("streaming —")) && !(hideSegments && l.includes("] [segment] "))).slice(-600);
  const base = `/jobs/${encodeURIComponent(jobId)}`;
  const stageEstimate = (name: string, stageStatus: string) => {
    if (!estimate || stageStatus === "completed") return "";
    if (estimate.current?.stage === name) return `~${roughDuration(estimate.current.remaining_seconds)} left`;
    const planned = estimate.pending.find((p) => p.stage === name);
    if (planned) return planned.seconds < 1 ? "" : `~${roughDuration(planned.seconds)}`;
    if (estimate.excludes.includes(name)) return "your review";
    return estimate.unknown_stages.includes(name) ? "?" : "";
  };

  return (
    <Shell jobId={jobId}>
      <main className="page">
        {info?.kind === "draft" && <NotStarted what="progress" />}
        {error && <div className="banner bad">{error}</div>}
        {started && !data && !error && <p className="meta">Loading…</p>}
        {data && status && (
          <>
            <section className="card summary">
              <div>
                <Chip kind={status.overall}>{status.overall}</Chip>
                <span className="meta" style={{ marginLeft: 6 }}>{data.source}</span>
                <div className="big">{current ? stageLabel(current.name) : "All stages complete"}</div>
                {current?.message && <div className="meta">{current.message}</div>}
                <Bar percent={(100 * done) / stages.length} running={status.overall !== "complete"} />
                <span className="meta">{done} of {stages.length} stages complete</span>
                {proc && (
                  <div className="meta" style={{ marginTop: 6 }}>
                    Dashboard-launched <b>{proc.label}</b> started {proc.started} — {OUTCOME_TEXT[proc.outcome] ?? `exited with code ${proc.exit_code}`}
                  </div>
                )}
                {proc?.outcome === "failed" && (
                  <div className="banner bad" style={{ marginTop: 10 }}>The {proc.label} command failed (exit code {proc.exit_code}):<pre>{proc.output_tail}</pre></div>
                )}
              </div>
              <div className="summary-side">
                {estimate && <EstimatePanel estimate={estimate} running={live} />}
                <div className="row" style={{ margin: 0, justifyContent: "flex-end" }}>
                {attention.glossary ? (
                  <Link to={`${base}/glossary`}><button className="primary">Review glossary</button></Link>
                ) : attention.review ? (
                  <Link to={`${base}/review`}><button className="primary">Open final review</button></Link>
                ) : null}
                </div>
              </div>
            </section>

            {live && data.progress.last_llm && (
              <Card title="Now">
                <div className="now">
                  {data.progress.last_llm}
                  {data.progress.last_rate ? `\nlast completed call: ${data.progress.last_rate} tok/s` : ""}
                </div>
              </Card>
            )}

            <Card title="Pipeline">
              <table className="grid stages">
                <thead>
                  <tr><th /><th>Stage</th><th>Work (this session)</th><th className="num">LLM calls</th><th className="num">Output tokens</th><th className="num">Time</th><th className="num">Estimate</th><th className="num">Attempts</th></tr>
                </thead>
                <tbody>
                  {stages.map((stage) => {
                    const activity = data.progress.stages[stage.name];
                    return (
                      <tr key={stage.name} className={stage.status}>
                        <td className={`icon ${stage.status}`}>{stage.status === "running" ? <span>◐</span> : ICONS[stage.status] ?? "○"}</td>
                        <td>
                          <b>{stageLabel(stage.name)}</b> <StageTip stage={stage.name} result={stageResult(stage, activity)} />{" "}
                          <span className="meta mono">{stage.name}</span>
                          {stage.message && <div className="msg">{stage.message}</div>}
                        </td>
                        <td className="work"><WorkCell stage={stage} activity={activity} /></td>
                        <td className="num">
                          {count(activity?.llm_calls)}
                          {activity?.avg_call_seconds ? <div className="meta">avg {activity.avg_call_seconds}s</div> : null}
                        </td>
                        <td className="num">{count(activity?.output_tokens)}</td>
                        <td className="num meta">{activity ? duration(activity.first, activity.last) : ""}</td>
                        <td className="num meta">{stageEstimate(stage.name, stage.status)}</td>
                        <td className="num meta">{stage.attempts || ""}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </Card>

            <section className="card">
              <div className="row" style={{ margin: "0 0 10px", justifyContent: "space-between" }}>
                <h2 style={{ margin: 0 }}>Session log <span className="meta mono" style={{ textTransform: "none" }}>{data.progress.log}</span></h2>
                <span className="row" style={{ margin: 0 }}>
                  <label className="meta"><input type="checkbox" checked={hideHeartbeat} onChange={(e) => setHideHeartbeat(e.target.checked)} /> hide streaming heartbeats</label>
                  <label className="meta"><input type="checkbox" checked={hideSegments} onChange={(e) => setHideSegments(e.target.checked)} /> hide per-segment results</label>
                  <label className="meta"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> follow</label>
                </span>
              </div>
              <div className="log logview" ref={logBox}>
                {shown.length ? shown.map((line, i) => <div key={i} className={lineClass(line)}>{line}</div>) : <span className="meta">No log lines yet.</span>}
              </div>
            </section>
          </>
        )}
      </main>
    </Shell>
  );
}
