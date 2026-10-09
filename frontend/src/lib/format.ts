import { getLocale, t } from "../i18n";

export function duration(from?: string, to?: string): string {
  if (!from || !to) return "";
  const s = Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000);
  if (s < 60) return t("format.duration.s", { s: Math.round(s) });
  if (s < 3600) return t("format.duration.ms", { m: Math.floor(s / 60), s: Math.round(s % 60) });
  return t("format.duration.hm", { h: Math.floor(s / 3600), m: Math.round((s % 3600) / 60) });
}

export function relativeTime(iso: string, now = Date.now()): string {
  if (!iso) return "";
  const s = (now - Date.parse(iso)) / 1000;
  if (s < 90) return t("format.ago.now");
  if (s < 5400) return t("format.ago.min", { n: Math.round(s / 60) });
  if (s < 129600) return t("format.ago.h", { n: Math.round(s / 3600) });
  return t("format.ago.d", { n: Math.round(s / 86400) });
}

export const count = (n?: number) => (n ? n.toLocaleString(getLocale()) : "");

/** Rough, readable span for estimates: "45 s", "12 min", "1 h 20 min". */
export function roughDuration(seconds: number): string {
  if (seconds < 60) return t("format.rough.s", { n: Math.max(1, Math.round(seconds)) });
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return t("format.rough.min", { n: minutes });
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? t("format.rough.hMin", { h: hours, m: rest }) : t("format.rough.h", { n: hours });
}

/** "en-zh" -> "EN → ZH", "pt-BR>ja" -> "PT-BR → JA"; an unknown or missing direction shows as a dash. */
export function directionLabel(direction: string | undefined): string {
  const text = direction ?? "";
  const [from, to] = text.includes(">") ? text.split(">") : text.split("-");
  return from && to ? `${from.toUpperCase()} → ${to.toUpperCase()}` : "—";
}

/** The files a job can be made from, as the file picker's `accept` and as a test of a name. */
export const SOURCE_ACCEPT = ".epub,.rtf,.txt,.md,.markdown,.html,.htm,.xhtml,.docx,.pdf,.srt,.vtt,.ass,.ssa";
export const isSubtitleFile = (name: string) => /\.(srt|vtt|ass|ssa)$/i.test(name);
export const isSourceBook = (name: string) => /\.(epub|rtf|txt|md|markdown|html?|xhtml|docx|pdf)$/i.test(name) || isSubtitleFile(name);
export const sourceKinds = () => t("format.sourceKinds");

/** The two kinds of job, as the server names them and as the dashboard shows them. */
export type JobType = "book" | "subtitles";
export const jobTypeLabel = (type: JobType) => t(type === "subtitles" ? "format.jobType.subtitles" : "format.jobType.book");

const OUTPUT_FORMATS = ["epub", "pdf", "docx", "html", "md", "txt", "srt", "ssa", "vtt", "ass"];
// A server from before a job had an output format does not say which it can write.
const FORMATS_BEFORE = ["epub", "docx", "html", "md", "txt"];

/** A book format's name: its own where it is one, translated where it is a description. */
export const outputFormatLabel = (kind: string): string =>
  kind === "docx" ? t("format.output.docx")
    : kind === "txt" ? t("format.output.txt")
    : kind === "md" ? "Markdown"
    : kind === "vtt" ? "WebVTT"
    : kind.toUpperCase();

/**
 * What a job's result can be downloaded as: `kinds` are the formats the server
 * can write it in. A book's are listed in a fixed order; a subtitle job's as the
 * server gives them, the file's own format first.
 */
export const outputFormats = (kinds: string[] = FORMATS_BEFORE): [string, string][] =>
  (kinds.includes("epub") ? OUTPUT_FORMATS.filter((kind) => kinds.includes(kind)) : kinds.filter((kind) => OUTPUT_FORMATS.includes(kind)))
    .map((kind) => [kind, outputFormatLabel(kind)]);

/** Download URL of a completed job's result: in the format the job gives back, or in `format`. */
export const outputUrl = (jobId: string, format = "") =>
  `/api/jobs/${encodeURIComponent(jobId)}/output${format ? `?format=${format}` : ""}`;

/** Download URL of the whole book's current translation (edits included) as XLIFF 2.1. */
export const xliffExportUrl = (jobId: string) => `/api/jobs/${encodeURIComponent(jobId)}/text/export?format=xliff`;

/** Download URL of an XLIFF import's per-unit CSV report. */
export const importReportUrl = (jobId: string, importId: string) =>
  `/api/jobs/${encodeURIComponent(jobId)}/text/import/report?import_id=${encodeURIComponent(importId)}`;

/** Compact local time for a table cell: "14:05" today, "Sep 18 14:05" this year, else "2025-09-18 14:05". */
export function shortTimestamp(iso: string, now = new Date()): string {
  const at = new Date(iso);
  if (!iso || Number.isNaN(at.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (at.toDateString() === now.toDateString()) return time;
  if (at.getFullYear() === now.getFullYear()) return `${at.toLocaleDateString(getLocale(), { month: "short", day: "numeric" })} ${time}`;
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${time}`;
}
