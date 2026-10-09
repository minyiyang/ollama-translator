import { useState } from "react";
import { rich, useT } from "../i18n";
import { importReportUrl } from "../lib/format";
import {
  categoryLabel,
  summarizeImport,
  type ImportFilter,
  type ImportOptions,
  type ImportPreview,
  type ImportResult,
  type SkippedItem,
} from "../lib/xliffImport";

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
  const t = useT();
  const summary = summarizeImport(preview.items, options);
  const unknown = preview.items.filter((item) => item.category === "unknown_id");
  const filters: [ImportFilter, string][] = [
    ["will_import", t("jobs.import.filter.willImport", { count: summary.willImport })],
    ["skipped", t("jobs.import.filter.skipped", { count: summary.skipped })],
    ["unchanged", t("jobs.import.filter.unchanged", { count: summary.unchanged })],
    ["not_in_file", t("jobs.import.filter.notInFile", { count: notInFile })],
    ["all", t("jobs.import.filter.all")],
  ];
  const optIn = (key: keyof ImportOptions, count: number, label: string) =>
    count > 0 && (
      <label className="ropt" key={key}>
        <input type="checkbox" checked={options[key]} onChange={(e) => onOptions({ ...options, [key]: e.target.checked })} /> {label}
      </label>
    );
  const canApply = summary.willImport > 0 && reason.trim().length >= 3 && !applying && !blocked;

  return (
    <section className="card import-card" aria-label={t("jobs.import.preview")}>
      <div className="row import-head">
        <h2>{t("jobs.import.previewOf", { file: preview.file_name })}</h2>
        <a className="button small" href={importReportUrl(jobId, preview.import_id)} download>{t("jobs.import.downloadReport")}</a>
      </div>
      {preview.error ? (
        <div className="banner bad">{t("jobs.import.cannotCheck", { error: preview.error })}</div>
      ) : (
        <>
          {preview.warnings.map((warning) => <div key={warning} className="banner warn">{warning}</div>)}
          <div className="segmented" role="radiogroup" aria-label={t("jobs.import.filter")}>
            {filters.map(([value, label]) => (
              <button key={value} type="button" role="radio" aria-checked={filter === value}
                className={filter === value ? "on" : ""} onClick={() => onFilter(value)}>
                {label}
              </button>
            ))}
          </div>
          {summary.skippedBy.length > 0 && (
            <p className="meta">
              {t("jobs.import.skippedBy", { list: summary.skippedBy.map(([category, n]) => t("jobs.import.categoryCount", { category: categoryLabel(category), count: n })).join(" · ") })}
            </p>
          )}
          {unknown.length > 0 && (
            <p className="meta">
              {t(unknown.length > 8 ? "jobs.import.unknownUnitsMore" : "jobs.import.unknownUnits", { count: unknown.length, ids: unknown.slice(0, 8).map((item) => item.unit_id).join(", ") })}
            </p>
          )}
          <div className="import-optins">
            {optIn("include_stale", summary.optIns.stale, t("jobs.import.includeStale", { count: summary.optIns.stale }))}
            {optIn("include_edited", summary.optIns.edited, t("jobs.import.includeEdited", { count: summary.optIns.edited }))}
            {optIn("include_overridable", summary.optIns.overridable, t("jobs.import.includeOverridable", { count: summary.optIns.overridable }))}
          </div>
        </>
      )}
      <div className="row import-apply">
        {!preview.error && (
          <>
            <input type="text" value={reason} onChange={(e) => onReason(e.target.value)}
              placeholder={t("jobs.import.reasonPlaceholder")} aria-label={t("jobs.import.reason")} />
            <span title={blocked}>
              <button className="primary" disabled={!canApply} onClick={onApply}>
                {applying ? t("jobs.import.importing") : t("jobs.import.apply", { count: summary.willImport })}
              </button>
            </span>
          </>
        )}
        <button className="small" disabled={applying} onClick={onCancel}>{t("jobs.import.cancel")}</button>
        {!preview.error && <span className="meta">{t("jobs.import.nothingWritten")}</span>}
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
  const t = useT();
  const [open, setOpen] = useState(false);
  return (
    <section className="card import-card" aria-label={t("jobs.import.last")}>
      <div className="row import-head">
        <span>
          {rich(result.dropped_at_apply > 0 ? "jobs.import.lastSummaryDropped" : "jobs.import.lastSummary", {
            file: result.file_name, applied: result.applied.length, skipped: result.skipped.length, dropped: result.dropped_at_apply,
            b: (chunks) => <b>{chunks}</b>,
          })}
        </span>
        <span className="spacer" />
        {result.skipped.length > 0 && (
          <button className="small" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
            {open ? t("jobs.import.hideSkipped") : t("jobs.import.showSkipped")}
          </button>
        )}
        <a className="button small" href={importReportUrl(jobId, result.import_id)} download>{t("jobs.import.downloadReport")}</a>
        <button className="small" onClick={onDismiss}>{t("jobs.import.dismiss")}</button>
      </div>
      {open && (
        <table className="grid import-skipped">
          <tbody>
            {result.skipped.map((item) => (
              <tr key={item.unit_id}>
                <td className="mono">{item.segment_id ?? item.unit_id}</td>
                <td><b>{categoryLabel(item.category)}</b>{item.message && <>: {item.message}</>}</td>
                <td>
                  {item.segment_id && (
                    <button className="small" onClick={() => onOpen(item)}
                      title={t("jobs.import.openTip")}>
                      {t("jobs.import.open")}
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
