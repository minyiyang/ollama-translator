import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { jobApi } from "../api";
import { useConfirm, useDialog } from "../components/Dialog";
import { LeftInSourceCard, useLeftInSource } from "../components/LeftInSource";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip, Highlight } from "../components/ui";
import { jobKey, rich, t as translate, useT, type MessageKey, type Translate } from "../i18n";
import { diffChars } from "../lib/diff";
import { findingCategoryLabel, findingOriginLabel, reviewKindLabel, severityLabel, statusLabel, versionLabel } from "../lib/enums";
import { FALLBACK_PAIR, langAttr, leftoverSourceText, pairCodes } from "../lib/languages";
import { cueLabel, readingProblems, type Cue, type ReadingLimits } from "../lib/subtitles";
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
  /** A subtitle job: the cue this passage belongs to. */
  cue?: Cue;
};
type CompileState = { state: string; events: { stage: string; status: string; message: string }[]; result: { result: string; message?: string; output?: string } | null };
type Payload = {
  worksheet: Worksheet | null; context: Record<string, Context>; stale: boolean; status: WorkflowStatus; compile: CompileState; compile_limit?: number;
  /** The final draft waits for approval and can be given it, whatever is in the queue. */
  approval_required?: boolean;
  /** A subtitle job: what fits a cue and can be read in its time. */
  reading?: ReadingLimits;
};
type Blocking = { category: string; severity: string; message: string };
type Item = { res: Resolution; edit: string; custom: string; customOn: boolean; check?: { key: string; blocking: Blocking[] } };

// Each preset is the reason as the worksheet records it, the same in every interface language, and the message that shows it.
const REASONS = {
  accept: [
    ["Verified against source; the audit finding is a false positive.", "review.reason.falsePositive"],
    ["Current wording is faithful; flagged difference is stylistic only.", "review.reason.stylisticOnly"],
  ],
  replace: [
    ["Corrected the mistranslation identified by the audit.", "review.reason.mistranslation"],
    ["Translated the text left in the source language.", "review.reason.leftInSource"],
    ["Restored meaning omitted from the source.", "review.reason.omission"],
  ],
} as const satisfies Record<string, readonly (readonly [string, MessageKey])[]>;
const REASON_GROUPS = [["review.reason.keepGroup", REASONS.accept], ["review.reason.fixGroup", REASONS.replace]] as const;
// The last is how that reason read when every book was English: a worksheet saved with it is not a custom reason.
const PRESETS: string[] = [...[...REASONS.accept, ...REASONS.replace].map(([reason]) => reason), "Translated the remaining English text."];

const edited = (it: Item) => it.edit !== it.res.current_translation;
const hasReason = (it: Item) => it.res.reason.trim().length >= 3;
const isResolved = (it: Item) => it.res.decision !== "pending" && hasReason(it);
const checkKey = (it: Item) => `${it.res.decision}|${it.res.decision === "accept" ? "" : it.edit}`;
const currentCheck = (it: Item) => (it.check && it.check.key === checkKey(it) ? it.check : undefined);

type Pair = { source: string; target: string; sourceName: string };

function lint(it: Item, pair: Pair, t: Translate): string[] {
  const out: string[] = [];
  if (!it.edit.trim()) out.push(t("review.lint.empty"));
  const left = leftoverSourceText(it.edit, pair.source, pair.target);
  if (left.length) out.push(t("review.lint.leftInSource", { language: pair.sourceName, words: left.slice(0, 5).join(", ") }));
  const digits = (s: string) => (s.match(/\d+(?:\.\d+)?/g) ?? []).sort().join(",");
  if (edited(it) && digits(it.edit) !== digits(it.res.current_translation)) out.push(t("review.lint.digitsChanged"));
  if (it.res.decision !== "pending" && !hasReason(it)) out.push(t("review.lint.reasonRequired"));
  return out;
}

function Diff({ before, after, lang }: { before: string; after: string; lang: string }) {
  return (
    <div className="diff" lang={lang}>
      {diffChars(before, after).map((part, i) =>
        part.kind === "same" ? <span key={i}>{part.text}</span> : part.kind === "del" ? <del key={i}>{part.text}</del> : <ins key={i}>{part.text}</ins>,
      )}
    </div>
  );
}

function CompileCard({ jobId, initial, jobType }: { jobId: string; initial?: CompileState; jobType?: string }) {
  const t = useT();
  const toast = useToast();
  const [state, setState] = useState<CompileState | undefined>(initial);
  const timer = useRef<number | undefined>(undefined);
  // Bumped when the card goes away: an answer that arrives afterwards is dropped and asks for nothing more.
  const visit = useRef(0);
  const poll = useCallback(async () => {
    const asked = visit.current;
    let next: CompileState;
    try {
      next = await jobApi<CompileState>(jobId, "review/compile");
    } catch (e) {
      if (asked !== visit.current) return;
      // Keep following: one failed request does not mean the compile stopped.
      toast("bad", translate("review.compile.checkFailed", { error: (e as Error).message }), 0);
      timer.current = window.setTimeout(poll, 1500);
      return;
    }
    if (asked !== visit.current) return;
    setState(next);
    if (next.state === "running") timer.current = window.setTimeout(poll, 1500);
    else if (next.result) toast(next.result.result === "complete" ? "ok" : "warn", translate("review.compile.finished", { result: statusLabel(next.result.result) }), 0);
    // `translate` is the language of the moment: depending on `t` would stop the polling when the language changes.
  }, [jobId, toast]);
  useEffect(() => {
    if (initial?.state === "running") poll();
    return () => { visit.current += 1; window.clearTimeout(timer.current); };
  }, [initial, poll]);
  const start = async () => {
    try { await jobApi(jobId, "review/compile", {}); poll(); } catch (e) { toast("bad", (e as Error).message, 0); }
  };
  const events = (state?.events ?? []).filter((e) => e.status !== "skipped");
  return (
    <section className="card">
      <h2>{t(jobKey("review.compile.title", jobType))}</h2>
      <p className="meta">{t("review.compile.help")}</p>
      <div className="row"><button className="primary" disabled={state?.state === "running"} onClick={start}>{t("review.compile.start")}</button></div>
      {(events.length > 0 || state?.result) && (
        <div className="log compile-log" style={{ marginTop: 10 }}>
          {events.map((e, i) => <div key={i}>{`${e.stage.padEnd(22)} ${e.status}${e.message ? `  ${e.message}` : ""}`}</div>)}
          {state?.result && (
            <div style={{ marginTop: 10 }}>
              → {state.result.result}{state.result.message ? `: ${state.result.message}` : ""}
              {state.result.output && <div>{t("review.compile.output", { path: state.result.output })}</div>}
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export function ReviewPage() {
  const t = useT();
  const { jobId, info } = useJob();
  const started = info?.kind === "job";
  // What the title stage left in the source language: listed here, translated on the Text tab.
  const leftInSource = useLeftInSource(jobId, started);
  const toast = useToast();
  const ask = useDialog();
  const confirm = useConfirm();
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
      setError("");
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

  // ``partial`` sends undecided segments (no decision or no reason) as pending.
  const serialize = (partial = false): Worksheet => ({
    ...data!.worksheet!,
    resolutions: items.map((it) => ({
      ...it.res,
      decision: partial && !isResolved(it) ? "pending" : it.res.decision,
      translated_text: it.res.decision === "replace" ? it.edit : null,
      replacements: [],
      reason: it.res.reason.trim(),
    })),
  });

  const save = async (quiet = false) => {
    try {
      await jobApi(jobId, "review/save", { worksheet: serialize() });
      setDirty(false);
      if (!quiet) toast("ok", t("review.draftSaved"));
    } catch (e) {
      toast("bad", t("review.saveFailed", { error: (e as Error).message }), 0);
    }
  };

  const limit = data?.compile_limit ?? 0;
  const undecided = items.filter((it) => !isResolved(it)).length;

  // Offered when the undecided segments fit within the compile limit.
  const askApproveNow = async () =>
    (await ask(
      t("review.withinLimit.title"),
      <>
        <p>{t("review.withinLimit.undecided", { count: undecided, limit })}</p>
        <p>{t("review.withinLimit.choice", { count: items.length - undecided })}</p>
      </>,
      [
        { value: "continue", label: t("review.withinLimit.continue"), primary: true },
        { value: "approve", label: t("review.withinLimit.approve") },
      ],
    )) === "approve";

  const apply = async () => {
    const pending = items.findIndex((it) => !isResolved(it));
    if (pending >= 0) {
      if (undecided <= limit && (await askApproveNow())) { await submit(true); return; }
      go(pending);
      toast("warn", t("review.decideFirst", { undecided, limit }));
      return;
    }
    await submit(false);
  };

  // Ask once each time the undecided count drops into the compile limit.
  const lastUndecided = useRef<number | null>(null);
  useEffect(() => {
    const before = lastUndecided.current;
    lastUndecided.current = active ? undecided : null;
    if (!active || before === null || busy) return;
    if (before > limit && undecided <= limit && undecided > 0) {
      askApproveNow().then((now) => { if (now) submit(true); });
    }
  });

  const submit = async (partial: boolean) => {
    setBusy(true);
    for (let i = 0; i < items.length; i++) {
      if (partial && !isResolved(items[i])) continue;
      const check = await runCheck(i);
      if (check?.blocking.length) {
        setBusy(false);
        go(i);
        toast("bad", t("review.wouldFail", { segment: items[i].res.segment_id }));
        return;
      }
    }
    try {
      const res = await jobApi<{ report: { passed: boolean; review_segment_ids: string[] }; approved: boolean }>(
        jobId, "review/apply", { worksheet: serialize(partial), approve_final: partial || approve, partial });
      setDirty(false);
      setApplied({ passed: res.report.passed, approved: res.approved, remaining: res.report.review_segment_ids });
      setData(await jobApi<Payload>(jobId, "review"));
    } catch (e) {
      toast("bad", <>{t("review.applyRefused")}<br />{(e as Error).message}</>, 0);
    } finally {
      setBusy(false);
    }
  };

  // Approval on its own: for a draft with no decision to apply with it.
  const approveDraft = async () => {
    setBusy(true);
    try {
      await jobApi(jobId, "review/approve", {});
      toast("ok", t("review.draftApproved"));
      await load();
    } catch (e) {
      toast("bad", <>{t("review.approvalRefused")}<br />{(e as Error).message}</>, 0);
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
        {t("review.progress", { decided, total: items.length })}
        <span className="bar"><i style={{ width: `${(100 * decided) / items.length}%` }} /></span>
      </span>
      <button onClick={() => save()} title={t("review.saveShortcut")}>{dirty ? t("review.saveDraftUnsaved") : t("review.saveDraft")}</button>
      <label className="meta"><input type="checkbox" checked={approve} onChange={(e) => setApprove(e.target.checked)} /> {t("review.approveToggle")}</label>
      <button className="primary" disabled={busy} onClick={apply}>{t("review.applyDecisions")}</button>
    </>
  ) : null;

  const shell = (children: ReactNode) => (
    <Shell jobId={jobId} tools={tools}>
      {children}
    </Shell>
  );

  if (info?.kind === "draft") return shell(<main className="page"><NotStarted what="review" /></main>);
  if (error) return shell(<main className="page"><div className="banner bad">{error}</div></main>);
  if (!data) return shell(<main className="page"><p className="meta">{t("common.loading")}</p></main>);

  if (!active) {
    const complete = data.status.overall === "complete";
    return shell(
      <main className="page">
        {applied ? (
          applied.passed ? (
            <div className="banner ok">{applied.approved ? t("review.applied.allApproved") : t("review.applied.all")}</div>
          ) : applied.approved ? (
            <div className="banner ok">{t("review.applied.approvedWithRemaining", { count: applied.remaining.length, segments: applied.remaining.join(", ") })}</div>
          ) : (
            <div className="banner warn">{t("review.applied.remaining", { count: applied.remaining.length, segments: applied.remaining.join(", ") })}</div>
          )
        ) : data.approval_required ? (
          <div className="banner warn">{t("review.state.approvalWaiting")}</div>
        ) : complete ? (
          <div className="banner ok">{t("review.state.complete")}</div>
        ) : (
          <div className="banner warn">
            {!data.worksheet ? t("review.state.noWorksheet") : data.stale ? t("review.state.stale") : t("review.state.empty")}
          </div>
        )}
        {data.approval_required && (
          <section className="card" aria-label={t("review.approve.title")}>
            <h2>{t("review.approve.title")}</h2>
            <p className="meta">{t("review.approve.help")}</p>
            <div className="row"><button className="primary" disabled={busy} onClick={approveDraft}>{t("review.approve.button")}</button></div>
          </section>
        )}
        <LeftInSourceCard jobId={jobId} items={leftInSource} />
        {!complete && <CompileCard jobId={jobId} initial={data.compile} jobType={info?.job_type} />}
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
  // The job's own languages; an older server names only the direction, or nothing.
  const [source, target] = info?.languages
    ? [info.languages.source.code, info.languages.target.code]
    : pairCodes(info?.direction || FALLBACK_PAIR.pair);
  const pair: Pair = { source, target, sourceName: info?.languages?.source.name ?? source.toUpperCase() };
  const [sourceLang, targetLang] = [source, target].map(langAttr);

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
      confirm(t("review.discardEdit.title"), t("review.discardEdit.body"), t("review.decision.accept")).then((ok) => {
        if (ok) patch(index, (x) => withRes({ ...x, edit: x.res.current_translation }, { decision }));
      });
      return;
    }
    if (decision === "replace" && !edited(it)) { toast("warn", t("review.editFirst")); return; }
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
      {resolvedCount > 0 && <div className="navhead">{t("review.nav.resolved", { count: resolvedCount })}</div>}
      {order.slice(0, resolvedCount).map(navItem)}
      {order.length - resolvedCount > 0 && <div className="navhead">{t("review.nav.toReview", { count: order.length - resolvedCount })}</div>}
      {order.slice(resolvedCount).map(navItem)}
    </>
  );

  return shell(
    <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:review" label={t("review.nav.label")} navRef={navRef}>
        <div className="seghead">
          <span className="title">{it.res.segment_id}</span>
          {ctx.cue && <span className="meta" title={t("review.cueTitle", { number: ctx.cue.number })}>{cueLabel(ctx.cue)}</span>}
          {ctx.chapter_title && <Chip>{ctx.chapter_title}</Chip>}
          {ctx.review_kind && <Chip>{reviewKindLabel(ctx.review_kind)}</Chip>}
          <span className="meta">{rich("review.position", { position: order.indexOf(index) + 1, total: items.length, kbd: (chunks) => <kbd>{chunks}</kbd> })}</span>
        </div>
        <div className="cols">
          <section className="card">
            <h2>{t("review.source")}</h2>
            {ctx.previous_source_context && <div className="ctx" lang={sourceLang}>{ctx.previous_source_context}</div>}
            <div className="text" lang={sourceLang}><Highlight text={it.res.source_text} quote={focus?.source_quote} /></div>
            {ctx.next_source_context && <div className="ctx" lang={sourceLang}>{ctx.next_source_context}</div>}
          </section>
          <section className="card">
            <h2>{t("review.current")}</h2>
            <div className="text zh" lang={targetLang}><Highlight text={it.res.current_translation} quote={focus?.translation_quote} /></div>
          </section>
        </div>

        <section className="card">
          <h2>{t("review.yours")}</h2>
          <textarea className="editor" lang={targetLang} spellCheck={false} value={it.edit} onChange={(e) => setEdit(e.target.value)} />
          <div className="row">
            <button className="small" onClick={() => setEdit(it.res.current_translation)}>{t("review.resetToCurrent")}</button>
            <span className="meta">{t("review.charCount", { count: [...it.edit].length, current: [...it.res.current_translation].length })}</span>
            <button className="small" onClick={() => runCheck(index)}>{t("review.check.run")}</button>
            <span className="lint">{[...lint(it, pair, t), ...readingProblems(it.edit, ctx.cue, data.reading)].join(" · ")}</span>
          </div>
          {check && it.res.decision !== "pending" && (
            check.blocking.length ? (
              <div className="check bad">
                {t("review.check.refused")}
                <ul>{check.blocking.map((b, i) => <li key={i}><b>{findingCategoryLabel(b.category)}</b> ({severityLabel(b.severity)}): {b.message}</li>)}</ul>
              </div>
            ) : <div className="check ok">{t("review.check.passes")}</div>
          )}
          {edited(it) && (
            <>
              <h2 style={{ marginTop: 14 }}>{t("review.changes")}</h2>
              <Diff before={it.res.current_translation} after={it.edit} lang={targetLang} />
            </>
          )}

          <h2 style={{ marginTop: 16 }}>{rich("review.reason.title", { note: (chunks) => <span className="meta" style={{ textTransform: "none" }}>{chunks}</span> })}</h2>
          <div className="reasons">
            {REASON_GROUPS.map(([label, list]) => (
              <div className="rgroup" key={label}>
                <span className="meta">{t(label)}</span>
                {list.map(([reason, shown]) => (
                  <label className="ropt" key={reason}>
                    <input type="radio" name="reason" checked={!custom && it.res.reason === reason} onChange={() => setReason(reason, false)} /> {t(shown)}
                  </label>
                ))}
              </div>
            ))}
            <div className="rgroup">
              <label className="ropt">
                <input type="radio" name="reason" checked={custom} onChange={() => setReason(it.custom, true)} /> {t("review.reason.custom")}
              </label>
              <input type="text" className="customreason" placeholder={t("review.reason.customPlaceholder")} value={it.custom}
                onFocus={() => { if (!custom) setReason(it.custom, true); }}
                onChange={(e) => setReason(e.target.value, true, e.target.value)} />
            </div>
          </div>
          <div className="row" style={{ marginTop: 14 }}>
            <div className="segmented">
              <button className={it.res.decision === "pending" ? "on" : ""} aria-pressed={it.res.decision === "pending"} onClick={() => setDecision("pending")}>{t("review.decision.pending")}</button>
              <button className={it.res.decision === "accept" ? "on" : ""} aria-pressed={it.res.decision === "accept"} disabled={!hasReason(it)} title={hasReason(it) ? "" : t("review.reason.selectFirst")} onClick={() => setDecision("accept")}>{t("review.decision.accept")}</button>
              <button className={it.res.decision === "replace" ? "on" : ""} aria-pressed={it.res.decision === "replace"} onClick={() => setDecision("replace")}>{t("review.decision.replace")}</button>
            </div>
            {!hasReason(it) && <span className="meta">{t("review.reason.selectToAccept")}</span>}
          </div>
        </section>

        <div className="cols">
          <section className="card">
            <h2>{t("review.findings.title", { count: findings.length })}</h2>
            {findings.map((f, i) => (
              <div key={i} className={`finding ${i === activeFinding ? "on" : ""}`} onClick={() => setActiveFinding(i === activeFinding ? -1 : i)}>
                {f.severity && <Chip kind={f.severity}>{severityLabel(f.severity)}</Chip>} {f.category && <Chip>{findingCategoryLabel(f.category)}</Chip>} {f.origin && <span className="meta">{findingOriginLabel(f.origin)}</span>}
                <div className="msg">{f.message}</div>
                {f.source_quote && <div className="quote" lang={sourceLang}>{source.toUpperCase()}: {f.source_quote}</div>}
                {f.translation_quote && <div className="quote" lang={targetLang}>{target.toUpperCase()}: {f.translation_quote}</div>}
              </div>
            ))}
          </section>
          <section className="card">
            <h2>{t("review.versions.title")}</h2>
            {versions.length ? versions.map((v, i) => (
              <div className="version" key={i}>
                <div className="row" style={{ margin: "0 0 6px" }}>
                  <Chip>{versionLabel(v.stage)}</Chip>
                  <button className="small" onClick={() => setEdit(v.text)}>{t("review.versions.load")}</button>
                </div>
                <div className="text zh" lang={targetLang}>{v.text}</div>
              </div>
            )) : <p className="meta">{t("review.versions.none")}</p>}
          </section>
        </div>
        <LeftInSourceCard jobId={jobId} items={leftInSource} />
    </SideLayout>,
  );
}
