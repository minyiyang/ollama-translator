import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, jobApi } from "../api";
import { useConfirm } from "../components/Dialog";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip } from "../components/ui";
import { diffChars } from "../lib/diff";
import { matchesTextQuery, matchesTextView, retainTextDocumentId, textRowClass, type TextView } from "../lib/text";

type Chapter = {
  document_id: string;
  order: number;
  title: string;
  segment_count: number;
  flagged_count: number;
  in_review_queue_count: number;
  edited_count: number;
  conflict_count: number;
};
type Outline = {
  available: boolean;
  editable: boolean;
  chapters: Chapter[];
  totals: { documents: number; segments: number; flagged: number; in_review_queue: number; edited: number; conflicts: number };
  uncompiled_edit_count: number;
};
type Finding = { category: string; severity: string; message: string };
type LastEdit = { action: string; author: string; at: string; reason: string; based_on: string };
type Segment = {
  segment_id: string;
  source: string;
  pipeline_text: string;
  text: string;
  state: "pipeline" | "edited" | "conflict" | "orphaned";
  edit_revision: string;
  base_target_sha256: string;
  findings: Finding[];
  flagged: boolean;
  in_review_queue: boolean;
  last_edit: LastEdit | null;
};
type ChapterDetail = { document_id: string; title: string; order: number; editable: boolean; segments: Segment[] };
type EditEvent = {
  event_id: string;
  at: string;
  author: string;
  action: "edit" | "revert" | "keep" | "take_pipeline";
  text: string;
  previous_text: string;
  reason: string;
  overrides: string[];
};

const STATE_CHIP: Record<Segment["state"], { kind: string; label: string } | null> = {
  pipeline: null,
  edited: { kind: "ok", label: "edited" },
  conflict: { kind: "bad", label: "conflict" },
  orphaned: { kind: "warn", label: "orphaned" },
};

function Diff({ before, after }: { before: string; after: string }) {
  return (
    <div className="diff" lang="zh-CN">
      {diffChars(before, after).map((part, i) =>
        part.kind === "same" ? <span key={i}>{part.text}</span> : part.kind === "del" ? <del key={i}>{part.text}</del> : <ins key={i}>{part.text}</ins>,
      )}
    </div>
  );
}

/** Book outline and paired source/translation view, editable once validate_repaired completes (docs/FULL_TEXT_REVIEW.md, phases 1-2). */
export function TextPage() {
  const { jobId, info } = useJob();
  const started = info?.kind === "job";
  const toast = useToast();
  const confirm = useConfirm();
  const [recompiling, setRecompiling] = useState(false);
  const [outline, setOutline] = useState<Outline | null>(null);
  const [error, setError] = useState("");
  const [documentId, setDocumentId] = useState("");
  const [detail, setDetail] = useState<ChapterDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [view, setView] = useState<TextView>("all");
  const [query, setQuery] = useState("");

  const [editingId, setEditingId] = useState<string | null>(null);
  const [editText, setEditText] = useState("");
  const [editReason, setEditReason] = useState("");
  const [overrideReason, setOverrideReason] = useState("");
  const [checkResult, setCheckResult] = useState<{ hard: Finding[]; overridable: Finding[] } | null>(null);
  const [saving, setSaving] = useState(false);
  const checkTimer = useRef<number | undefined>(undefined);

  const [historyId, setHistoryId] = useState<string | null>(null);
  const [historyEvents, setHistoryEvents] = useState<EditEvent[] | null>(null);
  const [revertReason, setRevertReason] = useState("");
  const [reverting, setReverting] = useState(false);

  const [conflictId, setConflictId] = useState<string | null>(null);
  const [conflictChoice, setConflictChoice] = useState<"keep" | "take_pipeline" | null>(null);
  const [conflictReason, setConflictReason] = useState("");
  const [resolvingConflict, setResolvingConflict] = useState(false);

  useEffect(() => {
    if (!started) return;
    let timer: number | undefined;
    const load = () =>
      jobApi<Outline>(jobId, "text")
        .then((payload) => {
          setOutline(payload);
          setError("");
          setDocumentId((current) => retainTextDocumentId(current, payload.chapters));
          timer = window.setTimeout(load, payload.editable ? 15000 : 5000);
        })
        .catch((e: Error) => setError(e.message));
    load();
    return () => window.clearTimeout(timer);
  }, [jobId, started]);

  const loadChapter = (id: string) =>
    jobApi<ChapterDetail>(jobId, `text/chapter?document_id=${encodeURIComponent(id)}`)
      .then((payload) => { setDetail(payload); setDetailError(""); })
      .catch((e: Error) => setDetailError(e.message));

  useEffect(() => {
    if (!documentId) return;
    setEditingId(null);
    setHistoryId(null);
    setConflictId(null);
    loadChapter(documentId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, documentId]);

  const refreshOutline = () => jobApi<Outline>(jobId, "text").then(setOutline).catch(() => {});

  const recompile = async () => {
    if (!(await confirm(
      "Recompile the book?",
      <p>Rebuilds the EPUB (or RTF) with your current edits and re-validates it. This only reruns compile and validation — no LLM calls, and takes seconds.</p>,
      "Recompile",
    ))) return;
    setRecompiling(true);
    try {
      await jobApi(jobId, "rerun", { stage: "compile" });
      toast("ok", <>Recompiling now. <Link to={`/jobs/${encodeURIComponent(jobId)}/progress`}>Follow it on the Progress tab →</Link></>);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setRecompiling(false);
    }
  };

  // -- filters and search ---------------------------------------------------

  const visible = useMemo(
    () => (detail?.segments ?? []).filter((s) => matchesTextView(s, view) && matchesTextQuery(s, query)),
    [detail, view, query],
  );

  // -- in-place editor --------------------------------------------------------

  const startEdit = (segment: Segment) => {
    setHistoryId(null);
    setConflictId(null);
    setEditingId(segment.segment_id);
    setEditText(segment.text);
    setEditReason("");
    setOverrideReason("");
    setCheckResult(null);
  };
  const cancelEdit = () => {
    setEditingId(null);
    window.clearTimeout(checkTimer.current);
  };
  const moveEdit = (delta: 1 | -1) => {
    if (!editingId) return;
    const index = visible.findIndex((s) => s.segment_id === editingId);
    const next = visible[index + delta];
    if (next) startEdit(next);
  };

  useEffect(() => {
    if (!editingId) return;
    window.clearTimeout(checkTimer.current);
    checkTimer.current = window.setTimeout(() => {
      jobApi<{ hard: Finding[]; overridable: Finding[] }>(jobId, "text/check", { segment_id: editingId, text: editText })
        .then(setCheckResult)
        .catch(() => setCheckResult(null));
    }, 400);
    return () => window.clearTimeout(checkTimer.current);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editingId, editText]);

  const editingSegment = detail?.segments.find((s) => s.segment_id === editingId) ?? null;
  const hardBlocked = (checkResult?.hard.length ?? 0) > 0;
  const needsOverride = (checkResult?.overridable.length ?? 0) > 0;
  const canSave =
    !!editingSegment && editReason.trim().length >= 3 && !hardBlocked && (!needsOverride || overrideReason.trim().length >= 3);

  const saveEdit = async () => {
    if (!editingSegment || !canSave) return;
    setSaving(true);
    try {
      await jobApi(jobId, "text/edit", {
        segment_id: editingSegment.segment_id,
        text: editText,
        reason: editReason,
        base_target_sha256: editingSegment.base_target_sha256,
        expected_event_id: editingSegment.edit_revision,
        ...(needsOverride ? { override_reason: overrideReason } : {}),
      });
      setEditingId(null);
      await loadChapter(documentId);
      await refreshOutline();
      toast("ok", "Edit saved.");
    } catch (e) {
      toast("bad", <>Not saved:<pre>{(e as ApiError).message}</pre></>, 0);
    } finally {
      setSaving(false);
    }
  };

  // -- history and revert ------------------------------------------------

  const openHistory = (segmentId: string) => {
    setEditingId(null);
    setConflictId(null);
    setHistoryId(segmentId);
    setRevertReason("");
    setHistoryEvents(null);
    jobApi<{ events: EditEvent[] }>(jobId, `text/history?segment=${encodeURIComponent(segmentId)}`)
      .then((payload) => setHistoryEvents(payload.events))
      .catch((e: Error) => toast("bad", e.message, 0));
  };
  const submitRevert = async (segmentId: string) => {
    const segment = detail?.segments.find((item) => item.segment_id === segmentId);
    if (!segment) return;
    setReverting(true);
    try {
      await jobApi(jobId, "text/revert", {
        segment_id: segmentId,
        reason: revertReason,
        expected_event_id: segment.edit_revision,
      });
      setHistoryId(null);
      await loadChapter(documentId);
      await refreshOutline();
      toast("ok", "Reverted to the pipeline translation.");
    } catch (e) {
      toast("bad", <>Not reverted:<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setReverting(false);
    }
  };

  // -- conflicts ------------------------------------------------------------

  const openConflict = (segmentId: string) => {
    setEditingId(null);
    setHistoryId(null);
    setConflictId(segmentId);
    setConflictChoice(null);
    setConflictReason("");
  };
  const submitConflict = async () => {
    if (!conflictId || !conflictChoice || conflictReason.trim().length < 3) return;
    const segment = detail?.segments.find((item) => item.segment_id === conflictId);
    if (!segment) return;
    setResolvingConflict(true);
    try {
      await jobApi(jobId, "text/conflict", {
        segment_id: conflictId,
        choice: conflictChoice,
        reason: conflictReason,
        expected_event_id: segment.edit_revision,
      });
      setConflictId(null);
      await loadChapter(documentId);
      await refreshOutline();
      toast("ok", conflictChoice === "keep" ? "Kept your edit." : "Took the new pipeline text.");
    } catch (e) {
      toast("bad", <>Not resolved:<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setResolvingConflict(false);
    }
  };

  // -- rendering ------------------------------------------------------------

  const sidebar = outline?.available ? (
    <>
      <div className="navhead">Chapters</div>
      {outline.chapters.map((chapter) => (
        <SideItem
          key={chapter.document_id}
          active={documentId === chapter.document_id}
          onClick={() => setDocumentId(chapter.document_id)}
          count={chapter.segment_count}
          sub={
            (chapter.conflict_count > 0 || chapter.in_review_queue_count > 0 || chapter.flagged_count > 0 || chapter.edited_count > 0) && (
              <>
                {chapter.conflict_count > 0 && <span>{chapter.conflict_count} conflict{chapter.conflict_count === 1 ? "" : "s"}</span>}
                {chapter.in_review_queue_count > 0 && <span>{chapter.conflict_count > 0 ? " · " : ""}{chapter.in_review_queue_count} in review</span>}
                {chapter.flagged_count > 0 && <span>{(chapter.conflict_count > 0 || chapter.in_review_queue_count > 0) ? " · " : ""}{chapter.flagged_count} flagged</span>}
                {chapter.edited_count > 0 && <span>{(chapter.conflict_count > 0 || chapter.in_review_queue_count > 0 || chapter.flagged_count > 0) ? " · " : ""}{chapter.edited_count} edited</span>}
              </>
            )
          }
        >
          {chapter.title || chapter.document_id}
        </SideItem>
      ))}
    </>
  ) : null;

  const editorRow = (segment: Segment) => (
    <tr className="editor-row">
      <td colSpan={4}>
        <div className="text-editor">
          <textarea
            className="editor"
            lang="zh-CN"
            spellCheck={false}
            autoFocus
            value={editText}
            onChange={(e) => setEditText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); saveEdit(); }
              else if (e.key === "Escape") { e.preventDefault(); cancelEdit(); }
              else if (e.altKey && e.key === "ArrowDown") { e.preventDefault(); moveEdit(1); }
              else if (e.altKey && e.key === "ArrowUp") { e.preventDefault(); moveEdit(-1); }
            }}
          />
          <div className="row">
            <button className="small" onClick={() => setEditText(segment.pipeline_text)}>Reset to pipeline text</button>
            <span className="meta">{[...editText].length} chars</span>
            <span className="meta"><kbd>Ctrl</kbd>+<kbd>Enter</kbd> save · <kbd>Esc</kbd> cancel · <kbd>Alt</kbd>+<kbd>↑</kbd>/<kbd>↓</kbd> move</span>
          </div>
          {checkResult && (checkResult.hard.length > 0 || checkResult.overridable.length > 0) && (
            <div className="check bad">
              {checkResult.hard.length > 0 && (
                <>
                  Cannot be saved:
                  <ul>{checkResult.hard.map((f, i) => <li key={i}><b>{f.category}</b> ({f.severity}): {f.message}</li>)}</ul>
                </>
              )}
              {checkResult.overridable.length > 0 && (
                <>
                  Needs an override reason:
                  <ul>{checkResult.overridable.map((f, i) => <li key={i}><b>{f.category}</b> ({f.severity}): {f.message}</li>)}</ul>
                </>
              )}
            </div>
          )}
          {checkResult && checkResult.hard.length === 0 && checkResult.overridable.length === 0 && (
            <div className="check ok">Passes deterministic validation.</div>
          )}
          {editText !== segment.pipeline_text && (
            <>
              <div className="navhead" style={{ marginTop: 10 }}>Changes vs pipeline text</div>
              <Diff before={segment.pipeline_text} after={editText} />
            </>
          )}
          {needsOverride && (
            <div className="field" style={{ marginTop: 10 }}>
              <label>Override reason (required to save past the findings above)</label>
              <input type="text" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} placeholder="Why this is fine despite the finding" />
            </div>
          )}
          <div className="field" style={{ marginTop: 10 }}>
            <label>Reason (required)</label>
            <input type="text" value={editReason} onChange={(e) => setEditReason(e.target.value)} placeholder="Why you are making this change" />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" disabled={!canSave || saving} onClick={saveEdit}>{saving ? "Saving…" : "Save"}</button>
            <button className="small" onClick={cancelEdit}>Cancel</button>
          </div>
        </div>
      </td>
    </tr>
  );

  const historyRow = (segment: Segment) => (
    <tr className="editor-row">
      <td colSpan={4}>
        {historyEvents === null ? (
          <p className="meta">Loading history…</p>
        ) : (
          <div className="text-history">
            {historyEvents.map((event) => (
              <div className="hist-event" key={event.event_id}>
                <div className="row" style={{ margin: 0 }}>
                  <Chip>{event.action}</Chip>
                  <span className="meta">{event.author} · {event.at}</span>
                </div>
                {event.text && <div className="text zh" lang="zh-CN">{event.text}</div>}
                <div className="meta">{event.reason}</div>
                {event.overrides.length > 0 && <div className="meta">Overrode: {event.overrides.join("; ")}</div>}
              </div>
            ))}
            {(segment.state === "edited" || segment.state === "conflict") && (
              <div className="row" style={{ marginTop: 10 }}>
                <input type="text" value={revertReason} onChange={(e) => setRevertReason(e.target.value)} placeholder="Reason for reverting (required)" />
                <button className="small" disabled={revertReason.trim().length < 3 || reverting} onClick={() => submitRevert(segment.segment_id)}>
                  {reverting ? "Reverting…" : "Revert to pipeline text"}
                </button>
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button className="small" onClick={() => setHistoryId(null)}>Close</button>
            </div>
          </div>
        )}
      </td>
    </tr>
  );

  const conflictRow = (segment: Segment) => {
    const basedOn = segment.last_edit?.based_on ?? "";
    return (
      <tr className="editor-row">
        <td colSpan={4}>
          <div className="text-editor">
            <p className="meta">
              A rerun changed this segment's translation after your edit. Choose which text to keep.
            </p>
            <div className="navhead">Your edit</div>
            <div className="text zh" lang="zh-CN">{segment.text}</div>
            <div className="navhead" style={{ marginTop: 10 }}>New pipeline text</div>
            <div className="text zh" lang="zh-CN">{segment.pipeline_text}</div>
            {basedOn && basedOn !== segment.pipeline_text && (
              <>
                <div className="navhead" style={{ marginTop: 10 }}>What changed (based-on text → new pipeline text)</div>
                <Diff before={basedOn} after={segment.pipeline_text} />
              </>
            )}
            <div className="row" style={{ marginTop: 12 }}>
              <div className="segmented" role="radiogroup" aria-label="Conflict resolution">
                <button type="button" role="radio" aria-checked={conflictChoice === "keep"} className={conflictChoice === "keep" ? "on" : ""} onClick={() => setConflictChoice("keep")}>Keep my edit</button>
                <button type="button" role="radio" aria-checked={conflictChoice === "take_pipeline"} className={conflictChoice === "take_pipeline" ? "on" : ""} onClick={() => setConflictChoice("take_pipeline")}>Use the new pipeline text</button>
              </div>
            </div>
            {conflictChoice && (
              <div className="field" style={{ marginTop: 10 }}>
                <label>Reason (required)</label>
                <input type="text" value={conflictReason} onChange={(e) => setConflictReason(e.target.value)} placeholder="Why you're resolving it this way" />
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button
                className="primary"
                disabled={!conflictChoice || conflictReason.trim().length < 3 || resolvingConflict}
                onClick={submitConflict}
              >
                {resolvingConflict ? "Resolving…" : "Resolve conflict"}
              </button>
              <button className="small" onClick={() => setConflictId(null)}>Cancel</button>
            </div>
          </div>
        </td>
      </tr>
    );
  };

  const body = (
    <>
      {info?.kind === "draft" && <NotStarted what="the book text" />}
      {error && <div className="banner bad">{error}</div>}
      {started && !outline && !error && <p className="meta">Loading…</p>}
      {outline && !outline.available && (
        <div className="banner info">
          The Text tab fills in once a translation exists. <Link to="progress">Follow progress →</Link>
        </div>
      )}
      {outline?.available && !outline.editable && (
        <div className="banner warn">
          Read-only: showing the latest translation stage. Editing opens once <b>Validate draft</b> completes.
        </div>
      )}
      {outline?.available && outline.uncompiled_edit_count > 0 && (
        <div className="banner warn">
          {outline.uncompiled_edit_count} edit{outline.uncompiled_edit_count === 1 ? "" : "s"} not in the compiled book.{" "}
          <button className="small" disabled={recompiling || info?.running} onClick={recompile}>
            {recompiling ? "Starting…" : "Recompile"}
          </button>
        </div>
      )}
      {outline?.available && (
        <>
          <section className="card">
            <div className="stats">
              <div className="stat"><b>{outline.totals.documents}</b><span>chapters</span></div>
              <div className="stat"><b>{outline.totals.segments}</b><span>segments</span></div>
              <div className="stat"><b>{outline.totals.flagged}</b><span>flagged</span></div>
              <div className="stat"><b>{outline.totals.in_review_queue}</b><span>in review queue</span></div>
              <div className="stat"><b>{outline.totals.edited}</b><span>edited</span></div>
              <div className="stat"><b>{outline.totals.conflicts}</b><span>conflicts</span></div>
            </div>
          </section>
          <section className="card">
            <div className="filters">
              <input
                type="text"
                placeholder="Search source or translation"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              <div className="segmented" role="radiogroup" aria-label="Filter segments">
                {(
                  [
                    ["all", "All"],
                    ["flagged", "Flagged"],
                    ["queue", "In review queue"],
                    ["edited", "Edited"],
                    ["conflict", "Conflicts"],
                  ] as [TextView, string][]
                ).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={view === value}
                    className={view === value ? "on" : ""}
                    onClick={() => setView(value)}
                  >
                    {label}
                  </button>
                ))}
              </div>
              {detail && <span className="meta">{visible.length} of {detail.segments.length} segments</span>}
            </div>
            {detailError && <div className="banner bad">{detailError}</div>}
            {detail && (
              <table className="grid text-pairs">
                <thead>
                  <tr><th className="sid">#</th><th>Source</th><th>Translation</th><th>Status</th></tr>
                </thead>
                <tbody>
                  {visible.map((segment) => {
                    const chip = STATE_CHIP[segment.state];
                    return (
                      <Fragment key={segment.segment_id}>
                        <tr
                          key={segment.segment_id}
                          className={textRowClass(segment)}
                        >
                          <td className="sid mono">{segment.segment_id}</td>
                          <td lang="en">{segment.source}</td>
                          <td lang="zh-CN">
                            {outline.editable ? (
                              <div className="editable-text" onClick={() => startEdit(segment)} title="Click to edit">
                                {segment.text}
                              </div>
                            ) : segment.text}
                          </td>
                          <td>
                            {chip && <Chip kind={chip.kind}>{chip.label}</Chip>}
                            {segment.in_review_queue && (
                              <>
                                <Chip kind="warn">in review</Chip>{" "}
                                <Link className="meta" to={`/jobs/${encodeURIComponent(jobId)}/review`}>Open in Final review →</Link>
                              </>
                            )}
                            {segment.flagged && !segment.in_review_queue && <Chip kind="warn">flagged</Chip>}
                            {segment.findings.map((finding, i) => (
                              <div className="meta" key={i}>{finding.message}</div>
                            ))}
                            <div className="row" style={{ marginTop: 4 }}>
                              {segment.state === "conflict" ? (
                                <button className="small danger" onClick={() => openConflict(segment.segment_id)}>Resolve conflict</button>
                              ) : (
                                outline.editable && <button className="small" onClick={() => startEdit(segment)}>Edit</button>
                              )}
                              {segment.state !== "pipeline" && <button className="small" onClick={() => openHistory(segment.segment_id)}>History</button>}
                            </div>
                          </td>
                        </tr>
                        {editingId === segment.segment_id && editorRow(segment)}
                        {historyId === segment.segment_id && historyRow(segment)}
                        {conflictId === segment.segment_id && conflictRow(segment)}
                      </Fragment>
                    );
                  })}
                  {visible.length === 0 && (
                    <tr><td colSpan={4} className="meta">No segments match.</td></tr>
                  )}
                </tbody>
              </table>
            )}
          </section>
        </>
      )}
    </>
  );

  return (
    <Shell jobId={jobId}>
      {sidebar ? (
        <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:text" label="Chapters">{body}</SideLayout>
      ) : (
        <main className="page">{body}</main>
      )}
    </Shell>
  );
}
