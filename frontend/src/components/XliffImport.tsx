import { useState } from "react";
import { importReportUrl } from "../lib/format";
import {
  CATEGORY_LABELS,
  summarizeImport,
  type ImportFilter,
  type ImportOptions,
  type ImportPreview,
  type ImportResult,
  type SkippedItem,
} from "../lib/xliffImport";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** The Text tab's import review card: counts that filter the table, opt-ins, and the apply form. */
export function ImportPreviewCard({
  jobId,
  preview,
  options,
  onOptions,
  filter,
  onFilter,
  notInFile,
  reason,
  onReason,
  applying,
  blocked,
  onApply,
  onCancel,
}: {
  jobId: string;
  preview: ImportPreview;
  options: ImportOptions;
  onOptions: (options: ImportOptions) => void;
  filter: ImportFilter;
  onFilter: (filter: ImportFilter) => void;
  notInFile: number;
  reason: string;
  onReason: (reason: string) => void;
  applying: boolean;
  /** Why applying is impossible right now (e.g. the job is running), or "". */
  blocked: string;
  onApply: () => void;
  onCancel: () => void;
}) {
  const summary = summarizeImport(preview.items, options);
  const unknown = preview.items.filter((item) => item.category === "unknown_id");
  const filters: [ImportFilter, string][] = [
    ["will_import", `Will import ${summary.willImport}`],
    ["skipped", `Skipped ${summary.skipped}`],
    ["unchanged", `Unchanged ${summary.unchanged}`],
    ["not_in_file", `Not in file ${notInFile}`],
    ["all", "All"],
  ];
  const optIn = (key: keyof ImportOptions, count: number, label: string) =>
    count > 0 && (
      <label className="ropt" key={key}>
        <input type="checkbox" checked={options[key]} onChange={(e) => onOptions({ ...options, [key]: e.target.checked })} /> {label}
      </label>
    );
  const canApply = summary.willImport > 0 && reason.trim().length >= 3 && !applying && !blocked;

  return (
    <section className="card import-card" aria-label="Import preview">
      <div className="row import-head">
        <h2>Import preview · {preview.file_name}</h2>
        <a className="button small" href={importReportUrl(jobId, preview.import_id)} download>Download report</a>
      </div>
      {preview.error ? (
        <div className="banner bad">This import can't be checked right now: {preview.error}</div>
      ) : (
        <>
          {preview.warnings.map((warning) => <div key={warning} className="banner warn">{warning}</div>)}
          <div className="segmented" role="radiogroup" aria-label="Import filter">
            {filters.map(([value, label]) => (
              <button key={value} type="button" role="radio" aria-checked={filter === value}
                className={filter === value ? "on" : ""} onClick={() => onFilter(value)}>
                {label}
              </button>
            ))}
          </div>
          {summary.skippedBy.length > 0 && (
            <p className="meta">
              Skipped: {summary.skippedBy.map(([category, n]) => `${CATEGORY_LABELS[category]} ${n}`).join(" · ")}
            </p>
          )}
          {unknown.length > 0 && (
            <p className="meta">
              {plural(unknown.length, "unit")} in the file {unknown.length === 1 ? "is" : "are"} not in this book:{" "}
              {unknown.slice(0, 8).map((item) => item.unit_id).join(", ")}
              {unknown.length > 8 && ", … (all are in the report)"}
            </p>
          )}
          <div className="import-optins">
            {optIn("include_stale", summary.optIns.stale,
              `Also import ${plural(summary.optIns.stale, "stale segment")} (the pipeline text changed after export)`)}
            {optIn("include_edited", summary.optIns.edited,
              `Also import ${plural(summary.optIns.edited, "segment")} edited in the Text tab after export (replaces those edits)`)}
            {optIn("include_overridable", summary.optIns.overridable,
              `Also import ${plural(summary.optIns.overridable, "segment")} with overridable findings (your reason is recorded as the override)`)}
          </div>
        </>
      )}
      <div className="row import-apply">
        {!preview.error && (
          <>
            <input type="text" value={reason} onChange={(e) => onReason(e.target.value)}
              placeholder="Reason for this import (required), e.g. Translator pass, Sept 2026" aria-label="Import reason" />
            <span title={blocked}>
              <button className="primary" disabled={!canApply} onClick={onApply}>
                {applying ? "Importing…" : `Import ${summary.willImport}`}
              </button>
            </span>
          </>
        )}
        <button className="small" disabled={applying} onClick={onCancel}>Cancel import</button>
        {!preview.error && <span className="meta">Nothing is written until you import.</span>}
      </div>
    </section>
  );
}

/** Summary of the last applied import, with its skipped segments until dismissed. */
export function LastImportCard({
  jobId,
  result,
  onOpen,
  onDismiss,
}: {
  jobId: string;
  result: ImportResult;
  onOpen: (item: SkippedItem) => void;
  onDismiss: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <section className="card import-card" aria-label="Last import">
      <div className="row import-head">
        <span>
          <b>Last import</b> · {result.file_name}: imported {result.applied.length}, skipped {result.skipped.length}
          {result.dropped_at_apply > 0 && ` (${result.dropped_at_apply} failed the checks when applied)`}
        </span>
        <span className="spacer" />
        {result.skipped.length > 0 && (
          <button className="small" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? "Hide skipped ▴" : "Show skipped ▾"}
          </button>
        )}
        <a className="button small" href={importReportUrl(jobId, result.import_id)} download>Download report</a>
        <button className="small" onClick={onDismiss}>Dismiss</button>
      </div>
      {open && (
        <table className="grid import-skipped">
          <tbody>
            {result.skipped.map((item) => (
              <tr key={item.unit_id}>
                <td className="mono">{item.segment_id ?? item.unit_id}</td>
                <td><b>{CATEGORY_LABELS[item.category]}</b>{item.message && <>: {item.message}</>}</td>
                <td>
                  {item.segment_id && (
                    <button className="small" onClick={() => onOpen(item)}
                      title="Go to this segment; where the file had a usable translation, the editor opens with it">
                      Open
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
