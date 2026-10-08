import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { StyleSheetSection, type StyleSheet } from "../components/StyleSheetSection";
import { Link } from "react-router-dom";
import { jobApi } from "../api";
import { useConfirm } from "../components/Dialog";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip, Highlight } from "../components/ui";
import { rich, t as translate, useT, type MessageKey, type Values } from "../i18n";
import { FALLBACK_PAIR, langAttr, pairCodes, type GlossaryPair } from "../lib/languages";

type Entry = { source: string; target: string; note: string; category: string; aliases: string[]; evidence: string[]; confidence: number };
type Record_ = { source: string; result: string; mode: string; reasons: string[]; target: string };
type Payload = {
  ready: boolean;
  approve_status: string;
  editable: boolean;
  review_mode: string;
  entries: Entry[];
  draft_entries: Entry[];
  /** The glossary's pair (an en/zh book's glossary is en-zh either way). */
  glossary_pair?: GlossaryPair | null;
  approval_records: Record_[];
  approval_summary: Record<string, number | string>;
  quality: { warnings?: string[]; suspicious_generic_terms?: string[] };
  evidence: Record<string, string>;
  categories: string[];
  category_labels: Record<string, string>;
  other_category: string;
  process: { label: string; running: boolean; outcome?: string; exit_code?: number | null; output_tail?: string } | null;
  approval_running: boolean;
  series_overlay?: { series_id: string; name: string; version: string } | null;
  style_sheet?: { draft: StyleSheet | null; approved: StyleSheet | null; review: "human" | "glossary"; pronouns: string[]; addresses: string[] };
};
/** keep: enforce this translation. defer: decide later (kept as drafted if approved).
 *  drop: not a glossary term (e.g. a generic word); the translator handles it in context. */
type Decision = "keep" | "defer" | "drop";
/** Why a drafted term deserves a second look: a message and its values. */
type Flag = { key: MessageKey; values?: Values };
type Row = { original: Entry; entry: Entry; decision: Decision; flags: Flag[]; record?: Record_; draft?: Entry };
type Saved = { source: string; entry: Entry; decision?: Decision; keep?: boolean };

const DECISIONS: [Decision, MessageKey, MessageKey][] = [
  ["keep", "glossary.decision.keep", "glossary.decision.keepTip"],
  ["defer", "glossary.decision.defer", "glossary.decision.deferTip"],
  ["drop", "glossary.decision.drop", "glossary.decision.dropTip"],
];
const GENERIC: MessageKey = "glossary.flag.generic";

const storeKey = (jobId: string) => `glossary-review:${jobId}`;

function readStore(jobId: string): Saved[] | null {
  try {
    const edits = JSON.parse(localStorage.getItem(storeKey(jobId)) ?? "null")?.edits ?? null;
    // Edits saved before the glossary sides were named source/target said english/chinese.
    return edits?.map((saved: any) => {
      const { english, chinese, ...rest } = saved.entry ?? {};
      return {
        ...saved,
        source: saved.source ?? saved.english,
        entry: english === undefined ? saved.entry : { ...rest, source: english, target: chinese },
      };
    }) ?? null;
  } catch { return null; }
}
function writeStore(jobId: string, rows: Row[] | null) {
  try {
    if (rows) localStorage.setItem(storeKey(jobId), JSON.stringify({ edits: rows.map((r) => ({ source: r.original.source, entry: r.entry, decision: r.decision })) }));
    else localStorage.removeItem(storeKey(jobId));
  } catch { /* storage unavailable: edits live only in this tab */ }
}

function buildRows(data: Payload, saved: ReturnType<typeof readStore>): Row[] {
  const savedBy = new Map((saved ?? []).map((e) => [e.source, e]));
  const records = new Map(data.approval_records.map((r) => [r.source, r]));
  const drafts = new Map(data.draft_entries.map((e) => [e.source, e]));
  const shared = new Map<string, number>();
  data.entries.forEach((e) => shared.set(e.target, (shared.get(e.target) ?? 0) + 1));
  const generic = new Set(data.quality.suspicious_generic_terms ?? []);
  return data.entries.map((original) => {
    const flags = [
      generic.has(original.source) && { key: GENERIC },
      original.confidence < 0.9 && { key: "glossary.flag.confidence", values: { value: original.confidence } },
      (shared.get(original.target) ?? 0) > 1 && { key: "glossary.flag.sharedTranslation" },
      !original.evidence?.length && { key: "glossary.flag.noEvidence" },
      original.category === data.other_category && { key: "glossary.flag.uncategorized" },
    ].filter(Boolean) as Flag[];
    const s = savedBy.get(original.source);
    return {
      original,
      entry: s ? { ...s.entry } : { ...original, aliases: [...(original.aliases ?? [])] },
      // Edits saved before Defer existed stored a keep flag.
      decision: s?.decision ?? (s?.keep === false ? "drop" : "keep"),
      flags,
      record: records.get(original.source),
      draft: drafts.get(original.source),
    };
  });
}

const changed = (r: Row) =>
  r.entry.target !== r.original.target || r.entry.category !== r.original.category || r.entry.note !== r.original.note ||
  (r.entry.aliases ?? []).join("|") !== (r.original.aliases ?? []).join("|");

export function GlossaryPage() {
  const t = useT();
  const { jobId, info, refresh: refreshJob } = useJob();
  const started = info?.kind === "job";
  const toast = useToast();
  const confirm = useConfirm();
  const [data, setData] = useState<Payload | null>(null);
  const [error, setError] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [open, setOpen] = useState<Set<number>>(new Set());
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState("");
  const [view, setView] = useState("all");
  const [submitting, setSubmitting] = useState(false);
  const [loadedAt, setLoadedAt] = useState(0);
  const [approvalMode, setApprovalMode] = useState<"" | "edited" | "draft" | "reviewed">("");
  /** The reviewer's style-sheet edits; null while untouched (the draft or LLM review applies). */
  const [styleEdit, setStyleEdit] = useState<StyleSheet | null>(null);

  const [reload, setReload] = useState(0);
  const wasRunning = useRef(false);

  // Loads the glossary; keeps polling while the draft is not ready or an approval runs.
  useEffect(() => {
    if (!started) return;
    let timer: number | undefined;
    let first = true;
    const load = () =>
      jobApi<Payload>(jobId, "glossary")
        .then((payload) => {
          setData(payload);
          if (!payload.ready) { timer = window.setTimeout(load, 5000); return; }
          if (payload.approval_running) {
            wasRunning.current = true;
            if (first) { setRows(buildRows(payload, null)); setLoadedAt(Date.now()); }
            first = false;
            timer = window.setTimeout(load, 3000);
            return;
          }
          if (wasRunning.current) {
            wasRunning.current = false;
            refreshJob();
            if (payload.approve_status === "completed") toast("ok", translate("glossary.toast.approved"));
          }
          const saved = payload.editable ? readStore(jobId) : null;
          setRows(buildRows(payload, saved));
          setLoadedAt(Date.now());
          if (saved && first) toast("info", translate("glossary.toast.restored"));
          first = false;
        })
        .catch((e: Error) => setError(e.message));
    load();
    return () => window.clearTimeout(timer);
  }, [jobId, toast, started, reload]); // eslint-disable-line react-hooks/exhaustive-deps

  const update = (index: number, patch: Partial<Row> | ((row: Row) => Row)) =>
    setRows((old) => {
      const next = old.map((row, i) => (i !== index ? row : typeof patch === "function" ? patch(row) : { ...row, ...patch }));
      writeStore(jobId, next);
      return next;
    });
  const edit = (index: number, field: keyof Entry, value: any) => update(index, (row) => ({ ...row, entry: { ...row.entry, [field]: value } }));

  const matchesQuery = useCallback((row: Row) => {
    const q = query.trim().toLowerCase();
    const e = row.entry;
    return !q || [e.source, e.target, e.note, ...(e.aliases ?? [])].some((v) => String(v ?? "").toLowerCase().includes(q));
  }, [query]);
  const matchesView = (row: Row, name: string) => {
    switch (name) {
      case "flagged": return row.flags.length > 0;
      case "edited": return changed(row);
      case "deferred": return row.decision === "defer";
      case "dropped": return row.decision === "drop";
      case "llm": return row.record?.mode === "llm";
      case "changed": return !!row.draft && row.draft.target !== row.original.target;
      default: return true;
    }
  };

  // Terms are grouped and counted by their drafted category, so editing a
  // term's category does not move it while you review.
  const home = (row: Row) => row.original.category;
  const catLabel = (value: string) => data?.category_labels?.[value] ?? value;
  const indexed = useMemo(() => rows.map((row, index) => ({ row, index })), [rows]);
  const searched = useMemo(() => indexed.filter(({ row }) => matchesQuery(row)), [indexed, matchesQuery]);
  const inView = searched.filter(({ row }) => matchesView(row, view));
  const matching = inView.filter(({ row }) => !category || home(row) === category);

  // The listed terms are fixed when you pick a filter, not on every decision:
  // keeping, deferring, or dropping a term never makes it jump out of the list.
  const [shown, setShown] = useState<Set<number> | null>(null);
  const refreshList = () => setShown(new Set(matching.map(({ index }) => index)));
  useEffect(() => { if (rows.length) refreshList(); }, [view, category, query, loadedAt]); // eslint-disable-line react-hooks/exhaustive-deps
  const visible = shown ? indexed.filter(({ index }) => shown.has(index)) : matching;
  const matchingIndexes = new Set(matching.map(({ index }) => index));
  const staleCount = visible.filter(({ index }) => !matchingIndexes.has(index)).length;

  // Sidebar counts stay live: categories within the current view, views within the current category.
  const categoryCounts = new Map<string, number>();
  inView.forEach(({ row }) => categoryCounts.set(home(row), (categoryCounts.get(home(row)) ?? 0) + 1));
  const viewCount = (name: string) => searched.filter(({ row }) => (!category || home(row) === category) && matchesView(row, name)).length;

  const categoryOrder = [...(data?.categories ?? []), ...[...categoryCounts.keys()].filter((c) => !data?.categories.includes(c))];
  const groups = categoryOrder
    .map((name) => ({ name, items: visible.filter(({ row }) => home(row) === name) }))
    .filter((group) => group.items.length > 0);
  const toggleGroup = (name: string) =>
    setCollapsed((old) => { const next = new Set(old); if (!next.delete(name)) next.add(name); return next; });

  const submit = async (body: { entries?: Entry[]; llm?: boolean; style?: StyleSheet }, title: string, detail: string, label: string) => {
    if (!(await confirm(title, <p>{detail}</p>, label))) return;
    setSubmitting(true);
    try {
      await jobApi(jobId, "glossary/approve", body);
      writeStore(jobId, null);
      setApprovalMode(body.llm ? (body.entries ? "edited" : "draft") : "reviewed");
      // Stay here: the tab switches to its "approval is running" state and follows it.
      setReload((n) => n + 1);
      refreshJob();
    } catch (e) {
      toast("bad", <>{t("glossary.toast.notSubmitted")}<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setSubmitting(false);
    }
  };
  /** The style sheet sent with an approval: always when a person must review it
   *  (approving is that review), otherwise only when edited. */
  const styleToSend = (): { style?: StyleSheet } => {
    const style = data?.style_sheet;
    const sheet = styleEdit ?? style?.draft;
    if (!sheet || !style) return {};
    return style.review === "human" || styleEdit ? { style: sheet } : {};
  };
  const reviewed = () =>
    rows.filter((r) => r.decision !== "drop").map((r) => ({ ...r.entry, aliases: (r.entry.aliases ?? []).map((a) => a.trim()).filter(Boolean) }));
  const deferredCount = rows.filter((r) => r.decision === "defer").length;
  const genericIndexes = rows.flatMap((r, i) => (r.flags.some((flag) => flag.key === GENERIC) && r.decision !== "drop" ? [i] : []));
  const dropGeneric = () => {
    setRows((old) => {
      const next = old.map((row, i) => (genericIndexes.includes(i) ? { ...row, decision: "drop" as Decision } : row));
      writeStore(jobId, next);
      return next;
    });
    toast("ok", t("glossary.toast.droppedGeneric", { count: genericIndexes.length }));
  };

  const progressLink = `/jobs/${encodeURIComponent(jobId)}/progress`;
  const editable = !!data?.editable;
  const summary = data?.approval_summary ?? {};
  const stats: [MessageKey, number | string][] = editable
    ? [
        ["glossary.stat.terms", rows.length],
        ["glossary.stat.kept", rows.filter((r) => r.decision === "keep").length],
        ["glossary.stat.deferred", deferredCount],
        ["glossary.stat.dropped", rows.filter((r) => r.decision === "drop").length],
        ["glossary.stat.edited", rows.filter((r) => r.decision !== "drop" && changed(r)).length],
        ["glossary.stat.flagged", rows.filter((r) => r.flags.length).length],
      ]
    : [
        ["glossary.stat.approvedTerms", data?.entries.length ?? 0],
        ["glossary.stat.autoApproved", summary.deterministic_count ?? "—"],
        ["glossary.stat.llmReviewed", summary.llm_count ?? "—"],
        ["glossary.stat.revised", summary.revised_count ?? "—"],
        ["glossary.stat.rejected", summary.rejected_count ?? "—"],
      ];
  const views: [string, MessageKey][] = editable
    ? [["all", "glossary.filter.all"], ["flagged", "glossary.filter.flagged"], ["edited", "glossary.filter.edited"], ["deferred", "glossary.filter.deferred"], ["dropped", "glossary.filter.dropped"]]
    : [["all", "glossary.filter.all"], ["llm", "glossary.filter.llmReviewed"], ["changed", "glossary.filter.llmChanged"], ["flagged", "glossary.filter.flaggedInDraft"]];
  const span = editable ? 8 : 6;
  const pair = data?.glossary_pair ?? FALLBACK_PAIR;
  const [sourceLang, targetLang] = pairCodes(pair.pair).map(langAttr);

  const evidenceRow = (row: Row) => (
    <tr className="evidence">
      <td colSpan={span}>
        {row.original.evidence?.length
          ? row.original.evidence.map((id) => (
              <div key={id}><span className="sid">{id}</span><Highlight text={data?.evidence[id] ?? t("glossary.evidence.unavailable")} quote={row.original.source} /></div>
            ))
          : <span className="meta">{t("glossary.evidence.none")}</span>}
      </td>
    </tr>
  );
  const toggleEvidence = (index: number) =>
    setOpen((old) => { const next = new Set(old); if (!next.delete(index)) next.add(index); return next; });

  const renderRow = ({ row, index }: { row: Row; index: number }) => {
                      const evidenceButton = (
                        <button className="small" onClick={() => toggleEvidence(index)}>
                          {row.original.evidence?.length ?? 0} {open.has(index) ? "▴" : "▾"}
                        </button>
                      );
                      if (editable) {
                        return (
                          <Fragment key={row.original.source}>
                            <tr className={`decision-${row.decision} ${changed(row) ? "changed" : ""} ${matchingIndexes.has(index) ? "" : "stale"}`}>
                              <td className="keep">
                                <div className="decision-pick" role="radiogroup" aria-label={t("glossary.decision.for", { term: row.entry.source })}>
                                  {DECISIONS.map(([value, label, tip]) => (
                                    <button key={value} type="button" role="radio" aria-checked={row.decision === value} title={t(tip)}
                                      className={`small ${value} ${row.decision === value ? "on" : ""}`}
                                      onClick={() => update(index, { decision: value })}>
                                      {t(label)}
                                    </button>
                                  ))}
                                </div>
                              </td>
                              <td className="en" lang={sourceLang}>{row.entry.source}</td>
                              <td className="zh"><input type="text" lang={targetLang} value={row.entry.target} onChange={(e) => edit(index, "target", e.target.value)} /></td>
                              <td className="cat">
                                <select value={row.entry.category} onChange={(e) => edit(index, "category", e.target.value)}>
                                  {data.categories.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}
                                </select>
                              </td>
                              <td className="note"><input type="text" value={row.entry.note} onChange={(e) => edit(index, "note", e.target.value)} /></td>
                              <td>
                                <input type="text" placeholder={t("glossary.aliasesPlaceholder")} defaultValue={(row.entry.aliases ?? []).join(", ")}
                                  onChange={(e) => edit(index, "aliases", e.target.value.split(",").map((a) => a.trim()).filter(Boolean))} />
                              </td>
                              <td>{row.flags.map((flag) => <span className="flag" key={flag.key}>{t(flag.key, flag.values)}</span>)}</td>
                              <td>{evidenceButton}</td>
                            </tr>
                            {open.has(index) && evidenceRow(row)}
                          </Fragment>
                        );
                      }
                      const record = row.record;
                      const llmChanged = !!row.draft && row.draft.target !== row.original.target;
                      return (
                        <Fragment key={row.original.source}>
                          <tr>
                            <td className="en" lang={sourceLang}>{row.original.source}</td>
                            <td lang={targetLang}>{llmChanged && <span className="was">{row.draft!.target}</span>}{row.original.target}</td>
                            <td>{catLabel(row.original.category)}</td>
                            <td className="meta">{row.original.note}</td>
                            <td>
                              {record ? (
                                <>
                                  <Chip kind={record.result === "approved" ? "ok" : "warn"}>{record.result}</Chip> <span className="meta">{record.mode}</span>
                                  {record.reasons.map((r) => <div className="meta" key={r}>{r.replace(/_/g, " ")}</div>)}
                                </>
                              ) : <span className="meta">—</span>}
                            </td>
                            <td>{evidenceButton}</td>
                          </tr>
                          {open.has(index) && evidenceRow(row)}
                        </Fragment>
                      );
  };

  const sidebar = data?.ready ? (
    <>
      <div className="navhead">{t("glossary.filter.categories")}</div>
      <SideItem active={!category} onClick={() => setCategory("")} count={inView.length}>{t("glossary.filter.allCategories")}</SideItem>
      {categoryOrder.map((name) => (
        <SideItem key={name} active={category === name} onClick={() => setCategory(category === name ? "" : name)} count={categoryCounts.get(name) ?? 0}>
          {catLabel(name)}
        </SideItem>
      ))}
      <div className="navhead">{t("glossary.filter.show")}</div>
      {views.map(([name, label]) => (
        <SideItem key={name} active={view === name} onClick={() => setView(name)} count={viewCount(name)}>{t(label)}</SideItem>
      ))}
    </>
  ) : null;

  const body = (
    <>
      {info?.kind === "draft" && <NotStarted what="glossary" />}
      {error && <div className="banner bad">{error}</div>}
      {data?.approval_running && (
        <div className="banner info approval-running" role="status">
          <span className="spinner" aria-hidden="true" />
          <div>
            {rich("glossary.approval.running", {
              mode: approvalMode,
              b: (chunks) => <b>{chunks}</b>,
              span: (chunks) => <span>{chunks}</span>,
              link: (chunks) => <Link to={progressLink}>{chunks}</Link>,
            })}
          </div>
        </div>
      )}
      {!data?.approval_running && data?.process?.label === "glossary approval" && data.process.outcome === "failed" && (
        <div className="banner bad">
          {t("glossary.approval.failed", { code: data.process.exit_code })}
          <pre>{data.process.output_tail}</pre>
        </div>
      )}
      {!data?.approval_running && data?.process?.running && data.process.label !== "glossary approval" && (
        <div className="banner info">{rich("glossary.processRunning", { label: data.process.label, link: (chunks) => <Link to={progressLink}>{chunks}</Link> })}</div>
      )}
      {started && !data && !error && <p className="meta">{t("common.loading")}</p>}
      {data && !data.ready && (
        <div className="banner warn">{rich("glossary.notReady", { link: (chunks) => <Link to={progressLink}>{chunks}</Link> })}</div>
      )}
      {data?.ready && data.editable && data.series_overlay && (
        <div className="banner info">
          {rich("glossary.seriesOverlay", {
            name: data.series_overlay.name,
            version: data.series_overlay.version,
            link: (chunks) => <Link to={`/series/${encodeURIComponent(data.series_overlay!.series_id)}`}>{chunks}</Link>,
            b: (chunks) => <b>{chunks}</b>,
          })}
        </div>
      )}
      {data?.ready && (
        <>
          <section className="card">
            <div className="row" style={{ margin: "0 0 12px", justifyContent: "space-between" }}>
              <div>
                <Chip kind={data.approve_status}>{data.approve_status === "paused" ? t("glossary.status.waitingForReview") : data.approve_status}</Chip>
                {data.review_mode && <span className="meta" style={{ marginLeft: 6 }}>{t("glossary.reviewMode", { mode: data.review_mode })}</span>}
              </div>
              {editable && (
                <div className="row" style={{ margin: 0 }}>
                  <button disabled={submitting} title={t("glossary.action.llmDraftTip")}
                    onClick={() => submit({ llm: true, ...styleToSend() }, t("glossary.confirm.llmDraft.title"), t("glossary.confirm.llmDraft.detail"), t("glossary.confirm.llmDraft.action"))}>{t("glossary.action.llmDraft")}</button>
                  <button disabled={submitting} title={t("glossary.action.llmEditsTip")}
                    onClick={() => submit({ entries: reviewed(), llm: true, ...styleToSend() }, t("glossary.confirm.llmEdits.title"), t("glossary.confirm.llmEdits.detail", { deferred: deferredCount }), t("glossary.confirm.llmEdits.action"))}>{t("glossary.action.llmEdits")}</button>
                  <button className="primary" disabled={submitting}
                    onClick={() => submit({ entries: reviewed(), ...styleToSend() }, t("glossary.confirm.approve.title"), t("glossary.confirm.approve.detail", { deferred: deferredCount }), t("glossary.confirm.approve.action"))}>{t("glossary.action.approve")}</button>
                </div>
              )}
            </div>
            <div className="stats">{stats.map(([label, n]) => <div className="stat" key={label}><b>{n}</b><span>{t(label)}</span></div>)}</div>
            {!!data.quality.warnings?.length && <ul className="warnings-list">{data.quality.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
              {editable && genericIndexes.length > 0 && (
                <div className="row">
                  <button className="small" onClick={dropGeneric} title={t("glossary.decision.dropTip")}>
                    {t("glossary.action.dropGeneric", { count: genericIndexes.length })}
                  </button>
                  <span className="meta">{genericIndexes.map((i) => rows[i].original.source).join(", ")}</span>
                </div>
              )}
          </section>

          {(() => {
            const style = data.style_sheet;
            const sheet = editable ? styleEdit ?? style?.draft : style?.approved ?? style?.draft;
            if (!style || !sheet) return null;
            return (
              <StyleSheetSection
                sourceLang={sourceLang}
                targetLang={targetLang}
                sheet={sheet}
                editable={editable}
                edited={styleEdit !== null}
                needsReview={editable && style.review === "human"}
                pronouns={style.pronouns}
                addresses={style.addresses}
                onChange={setStyleEdit}
                onReset={() => setStyleEdit(null)}
              />
            );
          })()}

          <section className="card">
            <div className="filters">
              <input type="text" placeholder={t("glossary.list.searchPlaceholder", { source: pair.source, target: pair.target })} value={query} onChange={(e) => setQuery(e.target.value)} />
              <span className="meta">{t("glossary.list.shown", { shown: visible.length, total: rows.length })}</span>
              {staleCount > 0 && (
                <span className="meta">
                  {rich("glossary.list.stale", { count: staleCount, button: (chunks) => <button className="small" onClick={refreshList}>{chunks}</button> })}
                </span>
              )}
              <span style={{ flex: 1 }} />
              <button className="small" onClick={() => setCollapsed(new Set())}>{t("glossary.list.expandAll")}</button>
              <button className="small" onClick={() => setCollapsed(new Set(groups.map((g) => g.name)))}>{t("glossary.list.collapseAll")}</button>
            </div>
            {groups.length === 0 ? (
              <p className="meta">{t("glossary.list.empty")}</p>
            ) : (
              <table className="grid term-table">
                <thead>
                  {editable ? (
                    <tr><th>{t("glossary.column.decision")}</th><th>{pair.source}</th><th>{pair.target}</th><th>{t("glossary.column.category")}</th><th>{t("glossary.column.note")}</th><th>{t("glossary.column.aliases")}</th><th>{t("glossary.column.flags")}</th><th>{t("glossary.column.evidence")}</th></tr>
                  ) : (
                    <tr><th>{pair.source}</th><th>{pair.target}</th><th>{t("glossary.column.category")}</th><th>{t("glossary.column.note")}</th><th>{t("glossary.column.approval")}</th><th>{t("glossary.column.evidence")}</th></tr>
                  )}
                </thead>
                {groups.map((group) => {
                  const closed = collapsed.has(group.name);
                  return (
                    <tbody className="group" key={group.name}>
                      <tr className={`group-head ${closed ? "closed" : ""}`}>
                        <td colSpan={span}>
                          <button type="button" aria-expanded={!closed} onClick={() => toggleGroup(group.name)}>
                            <span className="caret">▾</span>{catLabel(group.name)}<span className="meta">{group.items.length}</span>
                          </button>
                        </td>
                      </tr>
                      {!closed && group.items.map(renderRow)}
                    </tbody>
                  );
                })}
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
        <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:glossary" label={t("glossary.filter.sidebar")}>{body}</SideLayout>
      ) : (
        <main className="page">{body}</main>
      )}
    </Shell>
  );
}
