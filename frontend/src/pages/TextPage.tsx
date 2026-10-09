import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ApiError, jobApi } from "../api";
import { useConfirm } from "../components/Dialog";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip } from "../components/ui";
import { ImportPreviewCard, LastImportCard } from "../components/XliffImport";
import { rich, useT, type MessageKey } from "../i18n";
import { KIND_LABELS, chapterOf, noteNumber, pictureUrl, type BookItemKind } from "../lib/bookItems";
import { diffChars } from "../lib/diff";
import { editActionLabel, findingCategoryLabel, severityLabel } from "../lib/enums";
import { xliffExportUrl } from "../lib/format";
import { cueLabel, readingProblems, type Cue, type ReadingLimits } from "../lib/subtitles";
import { FALLBACK_PAIR, langAttr, pairCodes } from "../lib/languages";
import { matchesTextQuery, matchesTextView, retainTextDocumentId, textRowClass, type TextView } from "../lib/text";
import {
  categoryLabel,
  NO_OPT_INS,
  canPrefill,
  importsByChapter,
  matchesImportFilter,
  willImport,
  type ImportFilter,
  type ImportItem,
  type ImportOptions,
  type ImportPreview,
  type ImportResult,
  type ImportState,
  type SkippedItem,
} from "../lib/xliffImport";

type Chapter = {
  document_id: string;
  order: number;
  title: string;
  segment_count: number;
  flagged_count: number;
  in_review_queue_count: number;
  edited_count: number;
  conflict_count: number;
  /** The chapter's notes, and what of its notes and pictures' descriptions is still in the source language. */
  note_count?: number;
  untranslated_count?: number;
};
type Outline = {
  available: boolean;
  editable: boolean;
  chapters: Chapter[];
  totals: {
    documents: number; segments: number; flagged: number; in_review_queue: number; edited: number; conflicts: number;
    notes?: number; untranslated?: number;
  };
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
  /** A subtitle job: the cue this passage belongs to. */
  cue?: Cue;
  /** One of the book's own items (the title, a contents entry, a note, a picture's description), not a passage. */
  kind?: BookItemKind;
  /** Left in the source language by the title stage, and not translated since. */
  untranslated?: boolean;
  /** A passage: the notes it refers to, each by its first paragraph's id. */
  notes?: string[];
};
/** A paragraph of a note: which note it is of, and on its first paragraph, the passages that refer to it. */
type NoteRow = Segment & { note: string; referred_from: string[] };
/** A picture with its description: where its file is, and the passage it follows ("" at the start). */
type PictureRow = Segment & { picture_path: string; after: string };
type ChapterDetail = {
  document_id: string; title: string; order: number; editable: boolean; segments: Segment[];
  notes?: NoteRow[];
  pictures?: PictureRow[];
  /** A subtitle job: what fits a cue and can be read in its time. */
  reading?: ReadingLimits;
};
/** A row of the chapter's table, in the book's order: passages with their pictures, then the notes. */
type Row = { segment: Segment; kind: "passage" | "picture" | "note" };
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

const STATE_CHIP: Record<Segment["state"], { kind: string; label: MessageKey } | null> = {
  pipeline: null,
  edited: { kind: "ok", label: "text.state.edited" },
  conflict: { kind: "bad", label: "text.state.conflict" },
  orphaned: { kind: "warn", label: "text.state.orphaned" },
};

const VIEWS: [TextView, MessageKey][] = [
  ["all", "text.filter.all"],
  ["flagged", "text.filter.flagged"],
  ["queue", "text.filter.queue"],
  ["edited", "text.filter.edited"],
  ["conflict", "text.filter.conflict"],
  ["consistency", "text.filter.consistency"],
];

/** The server takes request bodies up to 8 MiB; leave room for JSON escaping. */
const MAX_IMPORT_BYTES = 7.5 * 1024 * 1024;

function Diff({ before, after, lang }: { before: string; after: string; lang: string }) {
  return (
    <div className="diff" lang={lang}>
      {diffChars(before, after).map((part, i) =>
        part.kind === "same" ? <span key={i}>{part.text}</span> : part.kind === "del" ? <del key={i}>{part.text}</del> : <ins key={i}>{part.text}</ins>,
      )}
    </div>
  );
}

/**
 * Book outline and paired source/translation view, editable once validate_repaired completes
 * (docs/FULL_TEXT_REVIEW.md). While an XLIFF import is pending the tab is in review mode
 * (docs/XLIFF_IMPORT.md): the preview card replaces the stats, and rows show the file's changes.
 */
export function TextPage() {
  const t = useT();
  const { jobId, info } = useJob();
  // The job's own languages; an older server names only the direction, or nothing.
  const [sourceLang, targetLang] = (
    info?.languages
      ? [info.languages.source.code, info.languages.target.code]
      : pairCodes(info?.direction || FALLBACK_PAIR.pair)
  ).map(langAttr);
  const started = info?.kind === "job";
  const toast = useToast();
  const confirm = useConfirm();
  const [recompiling, setRecompiling] = useState(false);
  const [outline, setOutline] = useState<Outline | null>(null);
  const [error, setError] = useState("");
  // Opened from elsewhere (the review page) at one chapter, and at one row of it.
  const [params] = useSearchParams();
  const [documentId, setDocumentId] = useState(params.get("chapter") ?? "");
  /** A row to bring into view once its chapter loads. */
  const pendingFocus = useRef<string | null>(params.get("item"));
  const [focusId, setFocusId] = useState<string | null>(null);
  /** A passage whose note is shown under it. */
  const [peek, setPeek] = useState<{ passage: string; note: string } | null>(null);
  const [detail, setDetail] = useState<ChapterDetail | null>(null);
  const [detailError, setDetailError] = useState("");
  const [view, setView] = useState<TextView>(params.get("view") === "untranslated" ? "untranslated" : "all");
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

  const [importState, setImportState] = useState<ImportState | null>(null);
  const [importOptions, setImportOptions] = useState<ImportOptions>(NO_OPT_INS);
  const [importFilter, setImportFilter] = useState<ImportFilter>("will_import");
  const [importReason, setImportReason] = useState("");
  const [importChecking, setImportChecking] = useState("");
  const [importApplying, setImportApplying] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  /** A segment to open in the editor once its chapter loads (Open on the Last import card). */
  const pendingOpen = useRef<{ segmentId: string; text: string | null } | null>(null);
  const pending = importState?.pending ?? null;
  const reviewing = !!pending;

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
      .then((payload) => { setDetail(payload); setDetailError(""); return payload; })
      .catch((e: Error) => { setDetailError(e.message); return null; });

  useEffect(() => {
    if (!documentId) return;
    setEditingId(null);
    setHistoryId(null);
    setConflictId(null);
    setPeek(null);
    loadChapter(documentId).then((payload) => {
      const open = pendingOpen.current;
      pendingOpen.current = null;
      const segment = open && payload?.segments.find((s) => s.segment_id === open.segmentId);
      if (open && segment) openForFix(segment, open.text);
      const focus = pendingFocus.current;
      pendingFocus.current = null;
      if (focus) bringIntoView(focus);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, documentId]);

  // A row asked for (a note, the passage that refers to it) is shown, and scrolled to.
  useEffect(() => {
    if (!focusId) return;
    document.getElementById(`row-${focusId}`)?.scrollIntoView?.({ block: "center" });
  }, [focusId, detail]);

  const bringIntoView = (rowId: string) => {
    setView("all");
    setQuery("");
    setFocusId(rowId);
  };

  /** Go to a row: in this chapter, or in the chapter its id names. */
  const goToRow = (rowId: string) => {
    if (allSegments.some((segment) => segment.segment_id === rowId)) {
      bringIntoView(rowId);
      return;
    }
    const chapter = outline ? chapterOf(rowId, outline.chapters) : "";
    if (!chapter) return;
    pendingFocus.current = rowId;
    setDocumentId(chapter);
  };

  /** A passage's note: shown under the passage when it is in this chapter, else its own chapter is opened at it. */
  const openNote = (passageId: string, noteId: string) => {
    if ((detail?.notes ?? []).some((note) => note.segment_id === noteId)) {
      setPeek((current) => (current?.passage === passageId && current.note === noteId ? null : { passage: passageId, note: noteId }));
      return;
    }
    goToRow(noteId);
  };

  const loadImportState = () =>
    jobApi<ImportState>(jobId, "text/import").then(setImportState).catch(() => {});

  useEffect(() => {
    if (started) loadImportState();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [jobId, started]);

  const refreshOutline = () => jobApi<Outline>(jobId, "text").then(setOutline).catch(() => {});

  const recompile = async () => {
    if (!(await confirm(
      t("text.recompile.title"),
      <p>{t("text.recompile.body")}</p>,
      t("text.recompile.action"),
    ))) return;
    setRecompiling(true);
    try {
      await jobApi(jobId, "rerun", { stage: "compile" });
      toast("ok", rich("text.recompile.started", { link: (chunks) => <Link to={`/jobs/${encodeURIComponent(jobId)}/progress`}>{chunks}</Link> }));
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    } finally {
      setRecompiling(false);
    }
  };

  // -- filters and search ---------------------------------------------------

  const importItems = useMemo(() => {
    const bySegment = new Map<string, ImportItem>();
    for (const item of pending?.items ?? []) if (item.segment_id) bySegment.set(item.segment_id, item);
    return bySegment;
  }, [pending]);

  // The chapter in the book's order: each picture after the passage it follows, the notes last.
  const rows = useMemo<Row[]>(() => {
    if (!detail) return [];
    const pictures = detail.pictures ?? [];
    const placed: Row[] = pictures.filter((picture) => !picture.after).map((segment) => ({ segment, kind: "picture" }));
    for (const segment of detail.segments) {
      placed.push({ segment, kind: "passage" });
      for (const picture of pictures) if (picture.after === segment.segment_id) placed.push({ segment: picture, kind: "picture" });
    }
    for (const note of detail.notes ?? []) placed.push({ segment: note, kind: "note" });
    return placed;
  }, [detail]);
  const allSegments = useMemo(() => rows.map((row) => row.segment), [rows]);
  const notesById = useMemo(() => new Map((detail?.notes ?? []).map((note) => [note.segment_id, note])), [detail]);

  const visibleRows = useMemo(
    () =>
      rows.filter(({ segment: s, kind }) =>
        (reviewing
          ? kind === "passage" && matchesImportFilter(importItems.get(s.segment_id), importFilter, importOptions)
          : matchesTextView(s, view))
        && matchesTextQuery(s, query),
      ),
    [rows, view, query, reviewing, importItems, importFilter, importOptions],
  );
  const visible = useMemo(() => visibleRows.map((row) => row.segment), [visibleRows]);

  // -- in-place editor --------------------------------------------------------

  /** Open the editor on a segment, optionally pre-filled with other text (e.g. from an import). */
  const openForFix = (segment: Segment, text: string | null) => {
    setView("all");
    setQuery("");
    startEdit(segment);
    if (text !== null) setEditText(text);
  };

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

  const editingSegment = allSegments.find((s) => s.segment_id === editingId) ?? null;
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
      toast("ok", t("text.edit.saved"));
    } catch (e) {
      toast("bad", rich("text.edit.notSaved", { detail: <pre>{(e as ApiError).message}</pre> }), 0);
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
    const segment = allSegments.find((item) => item.segment_id === segmentId);
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
      toast("ok", t("text.revert.done"));
    } catch (e) {
      toast("bad", rich("text.revert.failed", { detail: <pre>{(e as Error).message}</pre> }), 0);
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
    const segment = allSegments.find((item) => item.segment_id === conflictId);
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
      toast("ok", t(conflictChoice === "keep" ? "text.conflict.kept" : "text.conflict.took"));
    } catch (e) {
      toast("bad", rich("text.conflict.failed", { detail: <pre>{(e as Error).message}</pre> }), 0);
    } finally {
      setResolvingConflict(false);
    }
  };

  // -- XLIFF import -----------------------------------------------------------

  const chooseImportFile = async (file: File | undefined) => {
    if (fileInput.current) fileInput.current.value = "";
    if (!file) return;
    if (file.size > MAX_IMPORT_BYTES) {
      toast("bad", t("text.import.tooLarge", { name: file.name, size: (file.size / 1024 / 1024).toFixed(1) }), 0);
      return;
    }
    // A sticky error from an earlier file would otherwise read as this file's result.
    toast("info", null);
    setImportChecking(file.name);
    try {
      const preview = await jobApi<ImportPreview>(jobId, "text/import", { file_name: file.name, xliff: await file.text() });
      setEditingId(null);
      setHistoryId(null);
      setConflictId(null);
      setImportOptions(NO_OPT_INS);
      setImportFilter("will_import");
      setImportReason("");
      setImportState((current) => ({ pending: preview, last: current?.last ?? null }));
      toast("ok", t("text.import.checked", { name: file.name }));
    } catch (e) {
      toast("bad", rich("text.import.cannot", { name: file.name, detail: <pre>{(e as Error).message}</pre> }), 0);
    } finally {
      setImportChecking("");
    }
  };

  const applyImport = async () => {
    if (!pending) return;
    setImportApplying(true);
    try {
      const result = await jobApi<ImportResult>(jobId, "text/import/apply", {
        import_id: pending.import_id,
        reason: importReason,
        ...importOptions,
      });
      setImportState({ pending: null, last: result });
      toast("ok", t("text.import.done", { count: result.applied.length, dropped: result.dropped_at_apply }));
      if (documentId) await loadChapter(documentId);
      await refreshOutline();
    } catch (e) {
      toast("bad", rich("text.import.failed", { detail: <pre>{(e as Error).message}</pre> }), 0);
      await loadImportState();
    } finally {
      setImportApplying(false);
    }
  };

  const cancelImport = async () => {
    if (!pending) return;
    try {
      await jobApi(jobId, "text/import/cancel", { import_id: pending.import_id });
      setImportState((current) => ({ pending: null, last: current?.last ?? null }));
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    }
  };

  const dismissImport = async () => {
    const last = importState?.last;
    if (!last) return;
    try {
      await jobApi(jobId, "text/import/dismiss", { import_id: last.import_id });
      setImportState((current) => ({ pending: current?.pending ?? null, last: null }));
    } catch (e) {
      toast("bad", (e as Error).message, 0);
    }
  };

  const openSkipped = (item: SkippedItem) => {
    if (!item.segment_id || !item.document_id) return;
    const text = canPrefill(item.category) ? item.imported_text : null;
    if (item.document_id === documentId && detail) {
      const segment = detail.segments.find((s) => s.segment_id === item.segment_id);
      if (segment) openForFix(segment, text);
      return;
    }
    pendingOpen.current = { segmentId: item.segment_id, text };
    setDocumentId(item.document_id);
  };

  // -- rendering ------------------------------------------------------------

  const importCounts = useMemo(
    () => (pending ? importsByChapter(pending.items, importOptions) : new Map<string, number>()),
    [pending, importOptions],
  );

  /** What a chapter's entry says beside its name: what waits for someone, what was done, what it holds. */
  const chapterNotes = (chapter: Chapter): string[] =>
    (
      [
        ["text.chapter.conflicts", chapter.conflict_count],
        ["text.chapter.inReview", chapter.in_review_queue_count],
        ["text.chapter.flagged", chapter.flagged_count],
        ["text.chapter.untranslated", chapter.untranslated_count ?? 0],
        ["text.chapter.edited", chapter.edited_count],
        ["text.chapter.notes", chapter.note_count ?? 0],
      ] as [MessageKey, number][]
    ).filter(([, count]) => count > 0).map(([key, count]) => t(key, { count }));

  const sidebar = outline?.available ? (
    <>
      <div className="navhead">{t("text.chapters")}</div>
      {outline.chapters.map((chapter) => (
        <SideItem
          key={chapter.document_id}
          active={documentId === chapter.document_id}
          onClick={() => setDocumentId(chapter.document_id)}
          count={chapter.segment_count}
          sub={reviewing ? (
            (importCounts.get(chapter.document_id) ?? 0) > 0 && <span>{t("text.chapter.toImport", { count: importCounts.get(chapter.document_id) })}</span>
          ) : (
            chapterNotes(chapter).length > 0 && <span>{chapterNotes(chapter).join(" · ")}</span>
          )}
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
            lang={targetLang}
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
          {segment.kind === "note" && /<I\d{3}>/.test(segment.source) && (
            <p className="meta">{t("text.editor.keepMarkers")}</p>
          )}
          <div className="row">
            <button className="small" onClick={() => setEditText(segment.pipeline_text)}>{t("text.editor.reset")}</button>
            <span className="meta">{t("text.editor.chars", { count: [...editText].length })}</span>
            {readingProblems(editText, segment.cue, detail?.reading).map((problem) => (
              <span key={problem} className="lint">{problem}</span>
            ))}
            <span className="meta">{rich("text.editor.shortcuts", { kbd: (chunks) => <kbd>{chunks}</kbd> })}</span>
          </div>
          {checkResult && (checkResult.hard.length > 0 || checkResult.overridable.length > 0) && (
            <div className="check bad">
              {checkResult.hard.length > 0 && (
                <>
                  {t("text.editor.cannotSave")}
                  <ul>{checkResult.hard.map((f, i) => <li key={i}><b>{findingCategoryLabel(f.category)}</b> ({severityLabel(f.severity)}): {f.message}</li>)}</ul>
                </>
              )}
              {checkResult.overridable.length > 0 && (
                <>
                  {t("text.editor.needsOverride")}
                  <ul>{checkResult.overridable.map((f, i) => <li key={i}><b>{findingCategoryLabel(f.category)}</b> ({severityLabel(f.severity)}): {f.message}</li>)}</ul>
                </>
              )}
            </div>
          )}
          {checkResult && checkResult.hard.length === 0 && checkResult.overridable.length === 0 && (
            <div className="check ok">{t("text.editor.passes")}</div>
          )}
          {editText !== segment.pipeline_text && (
            <>
              <div className="navhead" style={{ marginTop: 10 }}>{t("text.editor.changes")}</div>
              <Diff lang={targetLang} before={segment.pipeline_text} after={editText} />
            </>
          )}
          {needsOverride && (
            <div className="field" style={{ marginTop: 10 }}>
              <label>{t("text.editor.overrideLabel")}</label>
              <input type="text" value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} placeholder={t("text.editor.overridePlaceholder")} />
            </div>
          )}
          <div className="field" style={{ marginTop: 10 }}>
            <label>{t("text.reasonRequired")}</label>
            <input type="text" value={editReason} onChange={(e) => setEditReason(e.target.value)} placeholder={t("text.editor.reasonPlaceholder")} />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button className="primary" disabled={!canSave || saving} onClick={saveEdit}>{saving ? t("common.saving") : t("common.save")}</button>
            <button className="small" onClick={cancelEdit}>{t("common.cancel")}</button>
          </div>
        </div>
      </td>
    </tr>
  );

  const historyRow = (segment: Segment) => (
    <tr className="editor-row">
      <td colSpan={4}>
        {historyEvents === null ? (
          <p className="meta">{t("text.history.loading")}</p>
        ) : (
          <div className="text-history">
            {historyEvents.map((event) => (
              <div className="hist-event" key={event.event_id}>
                <div className="row" style={{ margin: 0 }}>
                  <Chip>{editActionLabel(event.action)}</Chip>
                  <span className="meta">{event.author} · {event.at}</span>
                </div>
                {event.text && <div className="text zh" lang={targetLang}>{event.text}</div>}
                <div className="meta">{event.reason}</div>
                {event.overrides.length > 0 && <div className="meta">{t("text.history.overrode", { list: event.overrides.join("; ") })}</div>}
              </div>
            ))}
            {(segment.state === "edited" || segment.state === "conflict") && (
              <div className="row" style={{ marginTop: 10 }}>
                <input type="text" value={revertReason} onChange={(e) => setRevertReason(e.target.value)} placeholder={t("text.history.revertPlaceholder")} />
                <button className="small" disabled={revertReason.trim().length < 3 || reverting} onClick={() => submitRevert(segment.segment_id)}>
                  {reverting ? t("text.history.reverting") : t("text.history.revert")}
                </button>
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button className="small" onClick={() => setHistoryId(null)}>{t("common.close")}</button>
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
            <p className="meta">{t("text.conflict.explain")}</p>
            <div className="navhead">{t("text.conflict.yours")}</div>
            <div className="text zh" lang={targetLang}>{segment.text}</div>
            <div className="navhead" style={{ marginTop: 10 }}>{t("text.conflict.pipeline")}</div>
            <div className="text zh" lang={targetLang}>{segment.pipeline_text}</div>
            {basedOn && basedOn !== segment.pipeline_text && (
              <>
                <div className="navhead" style={{ marginTop: 10 }}>{t("text.conflict.changed")}</div>
                <Diff lang={targetLang} before={basedOn} after={segment.pipeline_text} />
              </>
            )}
            <div className="row" style={{ marginTop: 12 }}>
              <div className="segmented" role="radiogroup" aria-label={t("text.conflict.resolution")}>
                <button type="button" role="radio" aria-checked={conflictChoice === "keep"} className={conflictChoice === "keep" ? "on" : ""} onClick={() => setConflictChoice("keep")}>{t("text.conflict.keep")}</button>
                <button type="button" role="radio" aria-checked={conflictChoice === "take_pipeline"} className={conflictChoice === "take_pipeline" ? "on" : ""} onClick={() => setConflictChoice("take_pipeline")}>{t("text.conflict.take")}</button>
              </div>
            </div>
            {conflictChoice && (
              <div className="field" style={{ marginTop: 10 }}>
                <label>{t("text.reasonRequired")}</label>
                <input type="text" value={conflictReason} onChange={(e) => setConflictReason(e.target.value)} placeholder={t("text.conflict.reasonPlaceholder")} />
              </div>
            )}
            <div className="row" style={{ marginTop: 10 }}>
              <button
                className="primary"
                disabled={!conflictChoice || conflictReason.trim().length < 3 || resolvingConflict}
                onClick={submitConflict}
              >
                {resolvingConflict ? t("text.conflict.resolving") : t("text.conflict.resolve")}
              </button>
              <button className="small" onClick={() => setConflictId(null)}>{t("common.cancel")}</button>
            </div>
          </div>
        </td>
      </tr>
    );
  };

  const importRow = (segment: Segment, item: ImportItem | undefined) => {
    const changed = !!item && item.imported_text !== null && item.category !== "unchanged" && item.category !== "source_differs";
    const imports = !!item && willImport(item, importOptions);
    const findings = item ? [...item.hard, ...item.overridable] : [];
    return (
      <tr key={segment.segment_id} className={imports ? "import-will" : ""}>
        <td className="sid mono">{segment.segment_id}</td>
        <td lang={sourceLang}>
          {segment.source}
          {item?.category === "source_differs" && item.imported_source && (
            <div className="meta">{t("text.import.inFile", { source: item.imported_source })}</div>
          )}
        </td>
        <td lang={targetLang}>
          {changed ? <Diff lang={targetLang} before={item.current_text ?? segment.text} after={item.imported_text ?? ""} /> : segment.text}
        </td>
        <td>
          {!item ? (
            <span className="meta">{t("text.import.notInFile")}</span>
          ) : item.category === "unchanged" ? (
            <span className="meta">{t("text.import.unchanged")}</span>
          ) : (
            <>
              <Chip kind={imports ? "ok" : "warn"}>{imports ? t("text.import.willImport") : categoryLabel(item.category)}</Chip>
              {imports && item.category !== "import" && <span className="meta"> {t("text.import.inParens", { label: categoryLabel(item.category) })}</span>}
              {item.message && <div className="meta">{item.message}</div>}
              {findings.map((finding, i) => <div className="meta" key={i}>{finding.message}</div>)}
            </>
          )}
        </td>
      </tr>
    );
  };

  const body = (
    <>
      {info?.kind === "draft" && <NotStarted what="text" />}
      {error && <div className="banner bad">{error}</div>}
      {started && !outline && !error && <p className="meta">{t("common.loading")}</p>}
      {outline && !outline.available && (
        <div className="banner info">{rich("text.unavailable", { link: (chunks) => <Link to="progress">{chunks}</Link> })}</div>
      )}
      {outline?.available && !outline.editable && (
        <div className="banner warn">{rich("text.readOnly", { b: (chunks) => <b>{chunks}</b> })}</div>
      )}
      {outline?.available && outline.uncompiled_edit_count > 0 && (
        <div className="banner warn">
          {t("text.uncompiled", { count: outline.uncompiled_edit_count })}{" "}
          <button className="small" disabled={recompiling || info?.running} onClick={recompile}>
            {recompiling ? t("text.recompile.starting") : t("text.recompile.action")}
          </button>
        </div>
      )}
      {outline?.available && (
        <>
          {!reviewing && importState?.last && (
            <LastImportCard jobId={jobId} result={importState.last} onOpen={openSkipped} onDismiss={dismissImport} />
          )}
          {importChecking && <div className="banner info">{t("text.import.checking", { name: importChecking })}</div>}
          {pending ? (
            <ImportPreviewCard
              jobId={jobId}
              preview={pending}
              options={importOptions}
              onOptions={setImportOptions}
              filter={importFilter}
              onFilter={setImportFilter}
              notInFile={Math.max(0, outline.totals.segments - importItems.size)}
              reason={importReason}
              onReason={setImportReason}
              applying={importApplying}
              blocked={info?.running ? t("text.import.blockedRunning") : ""}
              onApply={applyImport}
              onCancel={cancelImport}
            />
          ) : (
          <section className="card">
            <div className="stats">
              <div className="stat"><b>{outline.totals.documents}</b><span>{t("text.stats.chapters")}</span></div>
              <div className="stat"><b>{outline.totals.segments}</b><span>{t("text.stats.segments")}</span></div>
              <div className="stat"><b>{outline.totals.flagged}</b><span>{t("text.stats.flagged")}</span></div>
              <div className="stat"><b>{outline.totals.in_review_queue}</b><span>{t("text.stats.inReviewQueue")}</span></div>
              <div className="stat"><b>{outline.totals.edited}</b><span>{t("text.stats.edited")}</span></div>
              <div className="stat"><b>{outline.totals.conflicts}</b><span>{t("text.stats.conflicts")}</span></div>
              {(outline.totals.notes ?? 0) > 0 && <div className="stat"><b>{outline.totals.notes}</b><span>{t("text.stats.notes")}</span></div>}
              {(outline.totals.untranslated ?? 0) > 0 && <div className="stat"><b>{outline.totals.untranslated}</b><span>{t("text.stats.untranslated")}</span></div>}
              {outline.editable && (
                <div className="stats-actions">
                  <a
                    className="button small"
                    href={xliffExportUrl(jobId)}
                    download
                    title={t("text.export.title")}
                  >
                    {t("text.export.label")}
                  </a>
                  <button
                    className="small"
                    disabled={!!importChecking}
                    onClick={() => fileInput.current?.click()}
                    title={t("text.import.title")}
                  >
                    {t("text.import.label")}
                  </button>
                  <input
                    ref={fileInput}
                    type="file"
                    accept=".xlf,.xliff,.xml"
                    hidden
                    data-testid="xliff-file"
                    onChange={(e) => chooseImportFile(e.target.files?.[0])}
                  />
                </div>
              )}
            </div>
          </section>
          )}
          <section className="card">
            <div className="filters">
              <input
                type="text"
                placeholder={t("text.search.placeholder")}
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
              {!reviewing && <div className="segmented" role="radiogroup" aria-label={t("text.filter.label")}>
                {[
                  ...VIEWS,
                  // What the title stage left in the source language: shown while there is some.
                  ...((outline.totals.untranslated ?? 0) > 0 || view === "untranslated" ? [["untranslated", "text.filter.untranslated"] as [TextView, MessageKey]] : []),
                ].map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={view === value}
                    className={view === value ? "on" : ""}
                    onClick={() => setView(value)}
                  >
                    {t(label)}
                  </button>
                ))}
              </div>}
              {detail && <span className="meta">{t("text.filter.count", { shown: visible.length, total: rows.length })}</span>}
            </div>
            {detailError && <div className="banner bad">{detailError}</div>}
            {detail && (
              <table className="grid text-pairs">
                <thead>
                  <tr><th className="sid">#</th><th>{t("text.table.source")}</th><th>{t("text.table.translation")}</th><th>{t("text.table.status")}</th></tr>
                </thead>
                <tbody>
                  {visibleRows.map(({ segment, kind }, index) => {
                    if (reviewing) return importRow(segment, importItems.get(segment.segment_id));
                    const chip = STATE_CHIP[segment.state];
                    const note = kind === "note" ? (segment as NoteRow) : null;
                    const picture = kind === "picture" ? (segment as PictureRow) : null;
                    const peeked = peek?.passage === segment.segment_id ? notesById.get(peek.note) : undefined;
                    return (
                      <Fragment key={segment.segment_id}>
                        {note && visibleRows[index - 1]?.kind !== "note" && (
                          <tr className="group-head"><td colSpan={4}>{t("text.notes.heading")}</td></tr>
                        )}
                        <tr
                          key={segment.segment_id}
                          id={`row-${segment.segment_id}`}
                          className={[textRowClass(segment), focusId === segment.segment_id ? "focus" : ""].filter(Boolean).join(" ")}
                        >
                          <td className="sid mono">
                            {segment.segment_id}
                            {segment.kind && (
                              <div><Chip>{segment.kind === "note" && noteNumber(segment.source) ? t("text.notes.numbered", { number: noteNumber(segment.source) }) : t(KIND_LABELS[segment.kind])}</Chip></div>
                            )}
                            {picture && picture.picture_path && (
                              <img className="thumb" src={pictureUrl(jobId, picture.picture_path)} alt={picture.source} loading="lazy" />
                            )}
                            {segment.cue && <div className="meta" title={t("text.subtitles.cueTitle", { number: segment.cue.number })}>{cueLabel(segment.cue)}</div>}
                          </td>
                          <td lang={sourceLang}>
                            {segment.source}
                            {(segment.notes ?? []).length > 0 && (
                              <div className="row note-refs" style={{ marginTop: 4 }}>
                                {(segment.notes ?? []).map((noteId) => {
                                  const number = noteNumber(notesById.get(noteId)?.source ?? "");
                                  const here = notesById.has(noteId);
                                  return (
                                    <button
                                      key={noteId}
                                      className="small"
                                      aria-expanded={peek?.passage === segment.segment_id && peek.note === noteId}
                                      title={here ? t("text.notes.showUnder") : t("text.notes.openInChapter")}
                                      onClick={() => openNote(segment.segment_id, noteId)}
                                    >
                                      {number
                                        ? t(here ? "text.notes.refNumbered" : "text.notes.refNumberedElsewhere", { number })
                                        : t(here ? "text.notes.ref" : "text.notes.refElsewhere")}
                                    </button>
                                  );
                                })}
                              </div>
                            )}
                            {note && note.referred_from.length > 0 && (
                              <div className="row" style={{ marginTop: 4 }}>
                                <span className="meta">{t("text.notes.referredFrom")}</span>
                                {note.referred_from.map((passageId) => (
                                  <button key={passageId} className="small" onClick={() => goToRow(passageId)}>↑ {passageId}</button>
                                ))}
                              </div>
                            )}
                          </td>
                          <td lang={targetLang}>
                            {outline.editable ? (
                              <div className="editable-text" onClick={() => startEdit(segment)} title={t("text.edit.clickToEdit")}>
                                {segment.text}
                              </div>
                            ) : segment.text}
                          </td>
                          <td>
                            {chip && <Chip kind={chip.kind}>{t(chip.label)}</Chip>}
                            {segment.in_review_queue && (
                              <>
                                <Chip kind="warn">{t("text.status.inReview")}</Chip>{" "}
                                <Link className="meta" to={`/jobs/${encodeURIComponent(jobId)}/review`}>{t("text.status.openInReview")}</Link>
                              </>
                            )}
                            {segment.untranslated ? (
                              <Chip kind="warn">{t("text.status.untranslated")}</Chip>
                            ) : (
                              segment.flagged && !segment.in_review_queue && <Chip kind="warn">{t("text.status.flagged")}</Chip>
                            )}
                            {segment.findings.map((finding, i) => (
                              <div className="meta" key={i}>{finding.message}</div>
                            ))}
                            <div className="row" style={{ marginTop: 4 }}>
                              {segment.state === "conflict" ? (
                                <button className="small danger" onClick={() => openConflict(segment.segment_id)}>{t("text.conflict.resolve")}</button>
                              ) : (
                                outline.editable && <button className="small" onClick={() => startEdit(segment)}>{t("common.edit")}</button>
                              )}
                              {segment.state !== "pipeline" && <button className="small" onClick={() => openHistory(segment.segment_id)}>{t("text.history.open")}</button>}
                            </div>
                          </td>
                        </tr>
                        {peeked && (
                          <tr className="note-peek">
                            <td />
                            <td lang={sourceLang}><span className="meta">{t("text.notes.numbered", { number: noteNumber(peeked.source) })}</span> {peeked.source}</td>
                            <td lang={targetLang}>{peeked.text}</td>
                            <td><button className="small" onClick={() => { setPeek(null); goToRow(peeked.segment_id); }}>{t("text.notes.goTo")}</button></td>
                          </tr>
                        )}
                        {editingId === segment.segment_id && editorRow(segment)}
                        {historyId === segment.segment_id && historyRow(segment)}
                        {conflictId === segment.segment_id && conflictRow(segment)}
                      </Fragment>
                    );
                  })}
                  {visible.length === 0 && (
                    <tr><td colSpan={4} className="meta">{t("text.empty")}</td></tr>
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
        <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:text" label={t("text.chapters")}>{body}</SideLayout>
      ) : (
        <main className="page">{body}</main>
      )}
    </Shell>
  );
}
