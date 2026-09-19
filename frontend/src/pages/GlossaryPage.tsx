import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { jobApi } from "../api";
import { useConfirm } from "../components/Dialog";
import { NotStarted, useJob } from "../components/JobContext";
import { Shell } from "../components/Shell";
import { SideItem, SideLayout } from "../components/SideLayout";
import { useToast } from "../components/Toast";
import { Chip, Highlight } from "../components/ui";

type Entry = { english: string; chinese: string; note: string; category: string; aliases: string[]; evidence: string[]; confidence: number };
type Record_ = { english: string; result: string; mode: string; reasons: string[]; chinese: string };
type Payload = {
  ready: boolean;
  approve_status: string;
  editable: boolean;
  review_mode: string;
  entries: Entry[];
  draft_entries: Entry[];
  approval_records: Record_[];
  approval_summary: Record<string, number | string>;
  quality: { warnings?: string[]; suspicious_generic_terms?: string[] };
  evidence: Record<string, string>;
  categories: string[];
  category_labels: Record<string, string>;
  other_category: string;
  process: { label: string; running: boolean; outcome?: string; exit_code?: number | null; output_tail?: string } | null;
  approval_running: boolean;
};
/** keep: enforce this translation. defer: decide later (kept as drafted if approved).
 *  drop: not a glossary term (e.g. a generic word); the translator handles it in context. */
type Decision = "keep" | "defer" | "drop";
type Row = { original: Entry; entry: Entry; decision: Decision; flags: string[]; record?: Record_; draft?: Entry };
type Saved = { english: string; entry: Entry; decision?: Decision; keep?: boolean };

const DECISIONS: [Decision, string, string][] = [
  ["keep", "✓ Keep", "Enforce this translation everywhere the term appears."],
  ["defer", "⏸ Defer", "Decide later. Deferred terms stay in the Deferred view; if you approve before deciding, they are kept as drafted."],
  ["drop", "✗ Drop", "Not a glossary term (for example a generic word). It is left out of the glossary and translated from context; the book text is not changed."],
];

const storeKey = (jobId: string) => `glossary-review:${jobId}`;

function readStore(jobId: string): Saved[] | null {
  try { return JSON.parse(localStorage.getItem(storeKey(jobId)) ?? "null")?.edits ?? null; } catch { return null; }
}
function writeStore(jobId: string, rows: Row[] | null) {
  try {
    if (rows) localStorage.setItem(storeKey(jobId), JSON.stringify({ edits: rows.map((r) => ({ english: r.original.english, entry: r.entry, decision: r.decision })) }));
    else localStorage.removeItem(storeKey(jobId));
  } catch { /* storage unavailable: edits live only in this tab */ }
}

function buildRows(data: Payload, saved: ReturnType<typeof readStore>): Row[] {
  const savedBy = new Map((saved ?? []).map((e) => [e.english, e]));
  const records = new Map(data.approval_records.map((r) => [r.english, r]));
  const drafts = new Map(data.draft_entries.map((e) => [e.english, e]));
  const shared = new Map<string, number>();
  data.entries.forEach((e) => shared.set(e.chinese, (shared.get(e.chinese) ?? 0) + 1));
  const generic = new Set(data.quality.suspicious_generic_terms ?? []);
  return data.entries.map((original) => {
    const flags = [
      generic.has(original.english) && "generic word",
      original.confidence < 0.9 && `confidence ${original.confidence}`,
      (shared.get(original.chinese) ?? 0) > 1 && "shared Chinese",
      !original.evidence?.length && "no evidence",
      original.category === data.other_category && "uncategorized",
    ].filter(Boolean) as string[];
    const s = savedBy.get(original.english);
    return {
      original,
      entry: s ? { ...s.entry } : { ...original, aliases: [...(original.aliases ?? [])] },
      // Edits saved before Defer existed stored a keep flag.
      decision: s?.decision ?? (s?.keep === false ? "drop" : "keep"),
      flags,
      record: records.get(original.english),
      draft: drafts.get(original.english),
    };
  });
}

const changed = (r: Row) =>
  r.entry.chinese !== r.original.chinese || r.entry.category !== r.original.category || r.entry.note !== r.original.note ||
  (r.entry.aliases ?? []).join("|") !== (r.original.aliases ?? []).join("|");

export function GlossaryPage() {
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
  const [approvalMode, setApprovalMode] = useState("");

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
            if (payload.approve_status === "completed") toast("ok", "Glossary approved. Translation continues on the Progress tab.");
          }
          const saved = payload.editable ? readStore(jobId) : null;
          setRows(buildRows(payload, saved));
          setLoadedAt(Date.now());
          if (saved && first) toast("info", "Restored your unsaved glossary edits from this browser.");
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
    return !q || [e.english, e.chinese, e.note, ...(e.aliases ?? [])].some((v) => String(v ?? "").toLowerCase().includes(q));
  }, [query]);
  const matchesView = (row: Row, name: string) => {
    switch (name) {
      case "flagged": return row.flags.length > 0;
      case "edited": return changed(row);
      case "deferred": return row.decision === "defer";
      case "dropped": return row.decision === "drop";
      case "llm": return row.record?.mode === "llm";
      case "changed": return !!row.draft && row.draft.chinese !== row.original.chinese;
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

  const submit = async (body: { entries?: Entry[]; llm?: boolean }, title: string, detail: string, label: string, note = "") => {
    if (!(await confirm(title, <p>{detail}{note}</p>, label))) return;
    setSubmitting(true);
    try {
      await jobApi(jobId, "glossary/approve", body);
      writeStore(jobId, null);
      setApprovalMode(body.llm ? (body.entries ? "LLM review of your edited glossary" : "LLM review of the draft") : "Applying your reviewed glossary");
      // Stay here: the tab switches to its "approval is running" state and follows it.
      setReload((n) => n + 1);
      refreshJob();
    } catch (e) {
      toast("bad", <>Not submitted:<pre>{(e as Error).message}</pre></>, 0);
    } finally {
      setSubmitting(false);
    }
  };
  const reviewed = () =>
    rows.filter((r) => r.decision !== "drop").map((r) => ({ ...r.entry, aliases: (r.entry.aliases ?? []).map((a) => a.trim()).filter(Boolean) }));
  const deferredCount = rows.filter((r) => r.decision === "defer").length;
  const deferNote = deferredCount ? ` ${deferredCount} deferred term${deferredCount === 1 ? " is" : "s are"} still undecided and will be kept as drafted.` : "";
  const genericIndexes = rows.flatMap((r, i) => (r.flags.includes("generic word") && r.decision !== "drop" ? [i] : []));
  const dropGeneric = () => {
    setRows((old) => {
      const next = old.map((row, i) => (genericIndexes.includes(i) ? { ...row, decision: "drop" as Decision } : row));
      writeStore(jobId, next);
      return next;
    });
    toast("ok", `Dropped ${genericIndexes.length} generic word${genericIndexes.length === 1 ? "" : "s"}; they will be translated from context. Keep any of them again to undo.`);
  };

  const progressLink = `/jobs/${encodeURIComponent(jobId)}/progress`;
  const editable = !!data?.editable;
  const summary = data?.approval_summary ?? {};
  const stats: [string, number | string][] = editable
    ? [
        ["terms", rows.length],
        ["kept", rows.filter((r) => r.decision === "keep").length],
        ["deferred", deferredCount],
        ["dropped", rows.filter((r) => r.decision === "drop").length],
        ["edited", rows.filter((r) => r.decision !== "drop" && changed(r)).length],
        ["flagged", rows.filter((r) => r.flags.length).length],
      ]
    : [
        ["approved terms", data?.entries.length ?? 0],
        ["auto-approved", summary.deterministic_count ?? "—"],
        ["LLM-reviewed", summary.llm_count ?? "—"],
        ["revised", summary.revised_count ?? "—"],
        ["rejected", summary.rejected_count ?? "—"],
      ];
  const views = editable
    ? [["all", "All terms"], ["flagged", "Needs attention"], ["edited", "Edited"], ["deferred", "Deferred"], ["dropped", "Dropped"]]
    : [["all", "All terms"], ["llm", "Reviewed by LLM"], ["changed", "Changed by LLM"], ["flagged", "Flagged in draft"]];
  const span = editable ? 8 : 6;

  const evidenceRow = (row: Row) => (
    <tr className="evidence">
      <td colSpan={span}>
        {row.original.evidence?.length
          ? row.original.evidence.map((id) => (
              <div key={id}><span className="sid">{id}</span><Highlight text={data?.evidence[id] ?? "(source text unavailable)"} quote={row.original.english} /></div>
            ))
          : <span className="meta">No evidence recorded.</span>}
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
                          <Fragment key={row.original.english}>
                            <tr className={`decision-${row.decision} ${changed(row) ? "changed" : ""} ${matchingIndexes.has(index) ? "" : "stale"}`}>
                              <td className="keep">
                                <div className="decision-pick" role="radiogroup" aria-label={`Decision for ${row.entry.english}`}>
                                  {DECISIONS.map(([value, label, tip]) => (
                                    <button key={value} type="button" role="radio" aria-checked={row.decision === value} title={tip}
                                      className={`small ${value} ${row.decision === value ? "on" : ""}`}
                                      onClick={() => update(index, { decision: value })}>
                                      {label}
                                    </button>
                                  ))}
                                </div>
                              </td>
                              <td className="en">{row.entry.english}</td>
                              <td className="zh"><input type="text" lang="zh-CN" value={row.entry.chinese} onChange={(e) => edit(index, "chinese", e.target.value)} /></td>
                              <td className="cat">
                                <select value={row.entry.category} onChange={(e) => edit(index, "category", e.target.value)}>
                                  {data.categories.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}
                                </select>
                              </td>
                              <td className="note"><input type="text" value={row.entry.note} onChange={(e) => edit(index, "note", e.target.value)} /></td>
                              <td>
                                <input type="text" placeholder="comma-separated" defaultValue={(row.entry.aliases ?? []).join(", ")}
                                  onChange={(e) => edit(index, "aliases", e.target.value.split(",").map((a) => a.trim()).filter(Boolean))} />
                              </td>
                              <td>{row.flags.map((f) => <span className="flag" key={f}>{f}</span>)}</td>
                              <td>{evidenceButton}</td>
                            </tr>
                            {open.has(index) && evidenceRow(row)}
                          </Fragment>
                        );
                      }
                      const record = row.record;
                      const llmChanged = !!row.draft && row.draft.chinese !== row.original.chinese;
                      return (
                        <Fragment key={row.original.english}>
                          <tr>
                            <td className="en">{row.original.english}</td>
                            <td lang="zh-CN">{llmChanged && <span className="was">{row.draft!.chinese}</span>}{row.original.chinese}</td>
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
      <div className="navhead">Categories</div>
      <SideItem active={!category} onClick={() => setCategory("")} count={inView.length}>All categories</SideItem>
      {categoryOrder.map((name) => (
        <SideItem key={name} active={category === name} onClick={() => setCategory(category === name ? "" : name)} count={categoryCounts.get(name) ?? 0}>
          {catLabel(name)}
        </SideItem>
      ))}
      <div className="navhead">Show</div>
      {views.map(([name, label]) => (
        <SideItem key={name} active={view === name} onClick={() => setView(name)} count={viewCount(name)}>{label}</SideItem>
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
            <b>Glossary approval is running.</b> {approvalMode && <span>{approvalMode}. </span>}
            The glossary is locked until it finishes; this page updates by itself.{" "}
            <Link to={progressLink}>Follow progress →</Link>
          </div>
        </div>
      )}
      {!data?.approval_running && data?.process?.label === "glossary approval" && data.process.outcome === "failed" && (
        <div className="banner bad">
          Glossary approval failed (exit code {data.process.exit_code}); your glossary is editable again.
          <pre>{data.process.output_tail}</pre>
        </div>
      )}
      {!data?.approval_running && data?.process?.running && data.process.label !== "glossary approval" && (
        <div className="banner info">{data.process.label} is running. <Link to={progressLink}>Follow progress →</Link></div>
      )}
      {started && !data && !error && <p className="meta">Loading…</p>}
      {data && !data.ready && (
        <div className="banner warn">The glossary draft is not ready yet; extraction and resolution run first. <Link to={progressLink}>Watch progress →</Link></div>
      )}
      {data?.ready && (
        <>
          <section className="card">
            <div className="row" style={{ margin: "0 0 12px", justifyContent: "space-between" }}>
              <div>
                <Chip kind={data.approve_status}>{data.approve_status === "paused" ? "waiting for review" : data.approve_status}</Chip>
                {data.review_mode && <span className="meta" style={{ marginLeft: 6 }}>review mode: {data.review_mode}</span>}
              </div>
              {editable && (
                <div className="row" style={{ margin: 0 }}>
                  <button disabled={submitting} title="Send the untouched draft to the LLM reviewer"
                    onClick={() => submit({ llm: true }, "LLM review the original draft?", "Your edits on this page are discarded; the LLM reviews the untouched draft.", "LLM review the draft")}>LLM review the draft</button>
                  <button disabled={submitting} title="The LLM reviews your edited list and may still revise or reject entries"
                    onClick={() => submit({ entries: reviewed(), llm: true }, "Send your edits to the LLM reviewer?", "The LLM reviews your edited list and may still revise or reject entries; the pipeline then continues.", "Send for LLM review", deferNote)}>LLM review my edits</button>
                  <button className="primary" disabled={submitting}
                    onClick={() => submit({ entries: reviewed() }, "Approve your reviewed glossary?", "The pipeline continues with this glossary.", "Approve & continue", deferNote)}>Approve my review &amp; continue</button>
                </div>
              )}
            </div>
            <div className="stats">{stats.map(([label, n]) => <div className="stat" key={label}><b>{n}</b><span>{label}</span></div>)}</div>
            {!!data.quality.warnings?.length && <ul className="warnings-list">{data.quality.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
              {editable && genericIndexes.length > 0 && (
                <div className="row">
                  <button className="small" onClick={dropGeneric} title={DECISIONS[2][2]}>
                    ✗ Drop {genericIndexes.length} generic word{genericIndexes.length === 1 ? "" : "s"}
                  </button>
                  <span className="meta">{genericIndexes.map((i) => rows[i].original.english).join(", ")}</span>
                </div>
              )}
          </section>

          <section className="card">
            <div className="filters">
              <input type="text" placeholder="Search English, Chinese, note, or alias" value={query} onChange={(e) => setQuery(e.target.value)} />
              <span className="meta">{visible.length} of {rows.length} terms</span>
              {staleCount > 0 && (
                <span className="meta">
                  · {staleCount} no longer match this view and stay until you{" "}
                  <button className="small" onClick={refreshList}>Refresh list</button>
                </span>
              )}
              <span style={{ flex: 1 }} />
              <button className="small" onClick={() => setCollapsed(new Set())}>Expand all</button>
              <button className="small" onClick={() => setCollapsed(new Set(groups.map((g) => g.name)))}>Collapse all</button>
            </div>
            {groups.length === 0 ? (
              <p className="meta">No terms match.</p>
            ) : (
              <table className="grid term-table">
                <thead>
                  {editable ? (
                    <tr><th>Decision</th><th>English</th><th>Chinese</th><th>Category</th><th>Note</th><th>Aliases</th><th>Flags</th><th>Evidence</th></tr>
                  ) : (
                    <tr><th>English</th><th>Chinese</th><th>Category</th><th>Note</th><th>Approval</th><th>Evidence</th></tr>
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
        <SideLayout sidebar={sidebar} storageKey="sidebar-collapsed:glossary" label="Filters">{body}</SideLayout>
      ) : (
        <main className="page">{body}</main>
      )}
    </Shell>
  );
}
