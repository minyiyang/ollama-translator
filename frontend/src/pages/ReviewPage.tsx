import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { jobApi } from "../api";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip, Highlight } from "../components/ui";
import { diffChars } from "../lib/diff";
import type { WorkflowStatus } from "../lib/stages";

type Decision = "pending" | "accept" | "replace";
type Resolution = {
  segment_id: string;
  source_text: string;
  current_translation: string;
  findings: string[];
  suggested_fixes: string[];
  decision: Decision;
  translated_text: string | null;
  replacements: unknown[];
  reason: string;
};
type Worksheet = { schema_version: number; workspace: string; draft_output_hash: string; instructions: string[]; resolutions: Resolution[] };
type Finding = { message: string; severity?: string; category?: string; origin?: string; source_quote?: string; translation_quote?: string };
type Context = {
  chapter_title?: string;
  review_kind?: string;
  previous_source_context?: string;
  next_source_context?: string;
  findings?: Finding[];
  translation_versions?: { stage: string; text: string }[];
};
type CompileState = { state: string; events: { stage: string; status: string; message: string }[]; result: { result: string; message?: string; output?: string } | null };
type Payload = { worksheet: Worksheet | null; context: Record<string, Context>; stale: boolean; status: WorkflowStatus; compile: CompileState };
type Blocking = { category: string; severity: string; message: string };
type Item = { res: Resolution; edit: string; custom: string; customOn: boolean; check?: { key: string; blocking: Blocking[] } };

const REASONS = {
  accept: ["Verified against source; the audit finding is a false positive.", "Current wording is faithful; flagged difference is stylistic only."],
  replace: ["Corrected the mistranslation identified by the audit.", "Translated the remaining English text.", "Restored meaning omitted from the source."],
};
const PRESETS = [...REASONS.accept, ...REASONS.replace];

const edited = (it: Item) => it.edit !== it.res.current_translation;
const hasReason = (it: Item) => it.res.reason.trim().length >= 3;
const isResolved = (it: Item) => it.res.decision !== "pending" && hasReason(it);
const checkKey = (it: Item) => `${it.res.decision}|${it.res.decision === "accept" ? "" : it.edit}`;
const currentCheck = (it: Item) => (it.check && it.check.key === checkKey(it) ? it.check : undefined);

function lint(it: Item): string[] {
  const out: string[] = [];
  if (!it.edit.trim()) out.push("translation is empty");
  const latin = [...new Set(it.edit.match(/[A-Za-z]{2,}/g) ?? [])];
  if (latin.length) out.push(`English left in text: ${latin.slice(0, 5).join(", ")}`);
  const digits = (s: string) => (s.match(/\d+(?:\.\d+)?/g) ?? []).sort().join(",");
  if (edited(it) && digits(it.edit) !== digits(it.res.current_translation)) out.push("digits changed from current");
  if (it.res.decision !== "pending" && !hasReason(it)) out.push("reason required");
  return out;
}

function Diff({ before, after }: { before: string; after: string }) {
  return (
    <div className="diff" lang="zh-CN">
      {diffChars(before, after).map((part, i) =>
        part.kind === "same" ? <span key={i}>{part.text}</span> : part.kind === "del" ? <del key={i}>{part.text}</del> : <ins key={i}>{part.text}</ins>,
      )}
    </div>
  );
}

function CompileCard({ jobId, initial }: { jobId: string; initial?: CompileState }) {
  const toast = useToast();
  const [state, setState] = useState<CompileState | undefined>(initial);
  const timer = useRef<number | undefined>(undefined);
  const poll = useCallback(async () => {
    const next = await jobApi<CompileState>(jobId, "review/compile");
    setState(next);
    if (next.state === "running") timer.current = window.setTimeout(poll, 1500);
    else if (next.result) toast(next.result.result === "complete" ? "ok" : "warn", `Compile finished: ${next.result.result}`, 0);
  }, [jobId, toast]);
  useEffect(() => {
    if (initial?.state === "running") poll();
    return () => window.clearTimeout(timer.current);
  }, [initial, poll]);
  const start = async () => {
    try { await jobApi(jobId, "review/compile", {}); poll(); } catch (e) { toast("bad", (e as Error).message, 0); }
  };
  const events = (state?.events ?? []).filter((e) => e.status !== "skipped");
  return (
    <section className="card">
      <h2>Compile EPUB</h2>
      <p className="meta">Resumes the job: compile → validate_epub. Runs in this server; you can keep the page open.</p>
      <div className="row"><button className="primary" disabled={state?.state === "running"} onClick={start}>Compile now</button></div>
      {(events.length > 0 || state?.result) && (
        <div className="log compile-log" style={{ marginTop: 10 }}>
          {events.map((e, i) => <div key={i}>{`${e.stage.padEnd(22)} ${e.status}${e.message ? `  ${e.message}` : ""}`}</div>)}
          {state?.result && (
            <div style={{ marginTop: 10 }}>
              → {state.result.result}{state.result.message ? `: ${state.result.message}` : ""}
              {state.result.output && <div>EPUB: {state.result.output}</div>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export function ReviewPage() {
  const { jobId, info } = useJob();
  const started = info?.kind === "job";
  const toast = useToast();
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [items, setItems] = useState<Item[]>([]);
  const [index, setIndex] = useState(0);
  const [activeFinding, setActiveFinding] = useState(-1);
  const [dirty, setDirty] = useState(false);
  const [approve, setApprove] = useState(true);
  const [applied, setApplied] = useState<{ passed: boolean; approved: boolean; remaining: string[] } | null>(null);
  const [busy, setBusy] = useState(false);
  const itemsRef = useRef(items);
  itemsRef.current = items;
  const navRef = useRef<HTMLElement>(null);

  const load = useCallback(async () => {
    try {
      const payload = await jobApi<Payload>(jobId, "review");
      setData(payload);
      setItems((payload.worksheet?.resolutions ?? []).map((res) => ({
        res,
        edit: res.translated_text ?? res.current_translation,
        custom: PRESETS.includes(res.reason) ? "" : res.reason,
        customOn: !!res.reason && !PRESETS.includes(res.reason),
      })));
      setDirty(false);
    } catch (e) {
      setError((e as Error).message);
    }
  }, [jobId]);
  useEffect(() => { if (started) load(); }, [load, started]);

  const active = data?.worksheet && !data.stale && items.length > 0 && !applied;

  const patch = (i: number, fn: (it: Item) => Item) => {
    setItems((old) => old.map((it, j) => (j === i ? fn(it) : it)));
    setDirty(true);
  };
  const withRes = (it: Item, res: Partial<Resolution>): Item => ({ ...it, res: { ...it.res, ...res } });

  // Preview the same deterministic audit resolve-review applies, without writing.
  const runCheck = useCallback(async (i: number) => {
    const it = itemsRef.current[i];
    if (!it || it.res.decision === "pending") return undefined;
    const key = checkKey(it);
    if (it.check?.key === key) return it.check;
    let check: Item["check"];
    try {
      const res = await jobApi<{ blocking: Blocking[] }>(jobId, "review/check", { segment_id: it.res.segment_id, decision: it.res.decision, text: it.edit });
      check = { key, blocking: res.blocking };
    } catch (e) {
      check = { key, blocking: [{ severity: "error", category: "check", message: (e as Error).message }] };
    }
    setItems((old) => old.map((x, j) => (j === i ? { ...x, check } : x)));
    return check;
  }, [jobId]);

  const item = items[index];
  const itemKey = item ? checkKey(item) : "";
  useEffect(() => {
    if (!active) return;
    const handle = window.setTimeout(() => runCheck(index), 900);
    return () => window.clearTimeout(handle);
  }, [active, index, itemKey, runCheck]);

  const order = useMemo(() => {
    const all = items.map((_, i) => i);
    return [...all.filter((i) => isResolved(items[i])), ...all.filter((i) => !isResolved(items[i]))];
  }, [items]);
  const go = (i: number) => {
    if (i < 0 || i >= items.length) return;
    setIndex(i);
    setActiveFinding(-1);
    window.scrollTo(0, 0);
  };
  const step = (delta: number) => {
    const at = order.indexOf(index);
    if (at + delta >= 0 && at + delta < order.length) go(order[at + delta]);
  };

  useEffect(() => {
    navRef.current?.querySelector(".item.active")?.scrollIntoView({ block: "nearest" });
  }, [index, order]);

  const serialize = (): Worksheet => ({
    ...data!.worksheet!,
    resolutions: items.map((it) => ({
      ...it.res,
      translated_text: it.res.decision === "replace" ? it.edit : null,
      replacements: [],
      reason: it.res.reason.trim(),
    })),
  });

  const save = async (quiet = false) => {
    try {
      await jobApi(jobId, "review/save", { worksheet: serialize() });
      setDirty(false);
      if (!quiet) toast("ok", "Draft saved.");
    } catch (e) {
      toast("bad", `Save failed: ${(e as Error).message}`, 0);
    }
  };

  const apply = async () => {
    const pending = items.findIndex((it) => !isResolved(it));
    if (pending >= 0) {
      go(pending);
      toast("warn", `Decide every segment with a reason first (${items.filter((it) => !isResolved(it)).length} left).`);
      return;
    }
    setBusy(true);
    for (let i = 0; i < items.length; i++) {
      const check = await runCheck(i);
      if (check?.blocking.length) {
        setBusy(false);
        go(i);
        toast("bad", `${items[i].res.segment_id} would fail deterministic validation; see the red box under the editor.`);
        return;
      }
    }
    try {
      const res = await jobApi<{ report: { passed: boolean; review_segment_ids: string[] }; approved: boolean }>(jobId, "review/apply", { worksheet: serialize(), approve_final: approve });
      setDirty(false);
      setApplied({ passed: res.report.passed, approved: res.approved, remaining: res.report.review_segment_ids });
      setData(await jobApi<Payload>(jobId, "review"));
    } catch (e) {
      toast("bad", <>Apply refused; nothing was changed.<br />{(e as Error).message}</>, 0);
    } finally {
      setBusy(false);
    }
  };

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") { e.preventDefault(); if (active) save(); }
      if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); step(1); }
      if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); step(-1); }
    };
    const onUnload = (e: BeforeUnloadEvent) => { if (dirty) e.preventDefault(); };
    window.addEventListener("keydown", onKey);
    window.addEventListener("beforeunload", onUnload);
    return () => { window.removeEventListener("keydown", onKey); window.removeEventListener("beforeunload", onUnload); };
  });

  const decided = items.filter(isResolved).length;
  const tools = active ? (
    <>
      <span className="progress-mini">
        {decided}/{items.length} decided
        <span className="bar"><i style={{ width: `${(100 * decided) / items.length}%` }} /></span>
      </span>
      <button onClick={() => save()} title="Ctrl+S">Save draft{dirty ? " •" : ""}</button>
      <label className="meta"><input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} /> approve final draft</label>
      <button className="primary" disabled={busy} onClick={apply}>Apply decisions</button>
    </>
  ) : null;

  const shell = (children: ReactNode) => (
    <Shell jobId={jobId} tools={tools}>
      {children}
    </Shell>
  );

  if (info?.kind === "draft") return shell(<main className="page"><NotStarted what="review queue" /></main>);
  if (error) return shell(<main className="page"><div className="banner bad">{error}</div></main>);
  if (!data) return shell(<main className="page"><p className="meta">Loading…</p></main>);

  if (!active) {
    const complete = data.status.overall === "complete";
    return shell(
      <main className="page">
        {applied ? (
          applied.passed ? (
            <div className="banner ok">All decisions applied. {applied.approved ? "Final draft approved. " : ""}Nothing left in the review queue.</div>
          ) : (
            <div className="banner warn">Applied, but {applied.remaining.length} segment(s) still need review: {applied.remaining.join(", ")}. Compile to regenerate the worksheet.</div>
          )
        ) : complete ? (
          <div className="banner ok">This job is complete; there is nothing left to review.</div>
        ) : (
          <div className="banner warn">
            {!data.worksheet ? "No final-review worksheet exists for this job yet."
              : data.stale ? "The review queue has been resolved or the draft changed since this worksheet was written. Compile to regenerate reports."
              : "The review queue is empty."}
          </div>
        )}
        {!complete && <CompileCard jobId={jobId} initial={data.compile} />}
      </main>,
    );
  }

  const it = items[index];
  const ctx = data.context[it.res.segment_id] ?? {};
  const seen = new Set<string>();
  const findings = (ctx.findings?.length ? ctx.findings : it.res.findings.map((message) => ({ message })) as Finding[])
    .filter((f) => !seen.has(f.message) && !!seen.add(f.message));
  const focus = findings[activeFinding];
  const versions = (ctx.translation_versions ?? []).filter((v) => v.text);
  const check = currentCheck(it);
  const resolvedCount = order.filter((i) => isResolved(items[i])).length;
  const custom = it.customOn || (it.res.reason !== "" && !PRESETS.includes(it.res.reason));

  const setEdit = (text: string) =>
    patch(index, (x) => {
      const next = { ...x, edit: text };
      const decision: Decision = edited(next) ? "replace" : x.res.decision === "replace" ? "pending" : x.res.decision;
      return withRes(next, { decision });
    });
  const setReason = (reason: string, customOn: boolean, customText?: string) =>
    patch(index, (x) => {
      const next = { ...x, customOn, custom: customText ?? x.custom, res: { ...x.res, reason } };
      // An acceptance without a reason is not a decision: fall back to pending.
      return next.res.decision === "accept" && !hasReason(next) ? withRes(next, { decision: "pending" }) : next;
    });
  const setDecision = (decision: Decision) => {
    if (decision === "accept" && !hasReason(it)) return;
    if (decision === "accept" && edited(it)) {
      if (!window.confirm("Accepting keeps the current translation and discards your edit. Continue?")) return;
      patch(index, (x) => withRes({ ...x, edit: x.res.current_translation }, { decision }));
      return;
    }
    if (decision === "replace" && !edited(it)) { toast("warn", "Edit the translation first: replace needs a changed text."); return; }
    patch(index, (x) => withRes(x, { decision }));
  };

  const navItem = (i: number) => {
    const x = items[i];
    const blocked = currentCheck(x)?.blocking.length;
    return (
      <button key={x.res.segment_id} className={`item ${i === index ? "active" : ""}`} onClick={() => go(i)}>
        <span className={`dot ${blocked ? "blocked" : isResolved(x) ? x.res.decision : "pending"}`} />
        <span className="id">{x.res.segment_id}</span>
        <span className="ch">{data.context[x.res.segment_id]?.chapter_title ?? ""}</span>
      </button>
    );
  };

  const sidebar = (
    <>
      {resolvedCount > 0 && <div className="navhead">Resolved ({resolvedCount})</div>}
      {order.slice(0, resolvedCount).map(navItem)}
      {order.length - resolvedCount > 0 && <div className="navhead">To review ({order.length - resolvedCount})</div>}
      {order.slice(resolvedCount).map(navItem)}
    </>
  );

  return shell(
    <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:review" label="Segments" navRef={navRef}>
        <div className="seghead">
          <span className="title">{it.res.segment_id}</span>
          {ctx.chapter_title && <Chip>{ctx.chapter_title}</Chip>}
          {ctx.review_kind && <Chip>{ctx.review_kind}</Chip>}
          <span className="meta">{order.indexOf(index) + 1} of {items.length} · <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> to move</span>
        </div>
        <div className="cols">
          <section className="card">
            <h2>Source</h2>
            {ctx.previous_source_context && <div className="ctx">{ctx.previous_source_context}</div>}
            <div className="text"><Highlight text={it.res.source_text} quote={focus?.source_quote} /></div>
            {ctx.next_source_context && <div className="ctx">{ctx.next_source_context}</div>}
          </section>
          <section className="card">
            <h2>Current translation</h2>
            <div className="text zh" lang="zh-CN"><Highlight text={it.res.current_translation} quote={focus?.translation_quote} /></div>
          </section>
        </div>

        <section className="card">
          <h2>Your translation</h2>
          <textarea className="editor" lang="zh-CN" spellCheck={false} value={it.edit} onChange={(e) => setEdit(e.target.value)} />
          <div className="row">
            <button className="small" onClick={() => setEdit(it.res.current_translation)}>Reset to current</button>
            <span className="meta">{[...it.edit].length} chars (current {[...it.res.current_translation].length})</span>
            <button className="small" onClick={() => runCheck(index)}>Check</button>
            <span className="lint">{lint(it).join(" · ")}</span>
          </div>
          {check && it.res.decision !== "pending" && (
            check.blocking.length ? (
              <div className="check bad">
                Would be refused by deterministic validation:
                <ul>{check.blocking.map((b, i) => <li key={i}><b>{b.category}</b> ({b.severity}): {b.message}</li>)}</ul>
              </div>
            ) : <div className="check ok">Passes deterministic validation.</div>
          )}
          {edited(it) && (
            <>
              <h2 style={{ marginTop: 14 }}>Changes vs current</h2>
              <Diff before={it.res.current_translation} after={it.edit} />
            </>
          )}

          <h2 style={{ marginTop: 16 }}>Reason <span className="meta" style={{ textTransform: "none" }}>(required before accepting)</span></h2>
          <div className="reasons">
            {([["Keep current translation", REASONS.accept], ["Fix translation", REASONS.replace]] as const).map(([label, list]) => (
              <div className="rgroup" key={label}>
                <span className="meta">{label}</span>
                {list.map((text) => (
                  <label className="ropt" key={text}>
                    <input type="radio" name="reason" checked={!custom && it.res.reason === text} onChange={() => setReason(text, false)} /> {text}
                  </label>
                ))}
              </div>
            ))}
            <div className="rgroup">
              <label className="ropt">
                <input type="radio" name="reason" checked={custom} onChange={() => setReason(it.custom, true)} /> Custom reason
              </label>
              <input type="text" className="customreason" placeholder="Describe the source-grounded reason (at least 3 characters)" value={it.custom}
                onFocus={() => { if (!custom) setReason(it.custom, true); }}
                onChange={(e) => setReason(e.target.value, true, e.target.value)} />
            </div>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <div className="segmented">
              <button className={it.res.decision === "pending" ? "on" : ""} onClick={() => setDecision("pending")}>Pending</button>
              <button className={it.res.decision === "accept" ? "on" : ""} disabled={!hasReason(it)} title={hasReason(it) ? "" : "Select or enter a reason first"} onClick={() => setDecision("accept")}>Accept current</button>
              <button className={it.res.decision === "replace" ? "on" : ""} onClick={() => setDecision("replace")}>Replace with edit</button>
            </div>
            {!hasReason(it) && <span className="meta">Select a reason to enable Accept.</span>}
          </div>
        </section>

        <div className="cols">
          <section className="card">
            <h2>Findings ({findings.length}) · click to highlight</h2>
            {findings.map((f, i) => (
              <div key={i} className={`finding ${i === activeFinding ? "on" : ""}`} onClick={() => setActiveFinding(i === activeFinding ? -1 : i)}>
                {f.severity && <Chip kind={f.severity}>{f.severity}</Chip>} {f.category && <Chip>{f.category}</Chip>} {f.origin && <span className="meta">{f.origin}</span>}
                <div className="msg">{f.message}</div>
                {f.source_quote && <div className="quote">EN: {f.source_quote}</div>}
                {f.translation_quote && <div className="quote" lang="zh-CN">ZH: {f.translation_quote}</div>}
              </div>
            ))}
          </section>
          <section className="card">
            <h2>Pipeline versions</h2>
            {versions.length ? versions.map((v, i) => (
              <div className="version" key={i}>
                <div className="row" style={{ margin: "0 0 6px" }}>
                  <Chip>{v.stage}</Chip>
                  <button className="small" onClick={() => setEdit(v.text)}>Load into editor</button>
                </div>
                <div className="text zh" lang="zh-CN">{v.text}</div>
              </div>
            )) : <p className="meta">No alternative versions recorded.</p>}
          </section>
        </div>
    </SideLayout>,
  );
}
