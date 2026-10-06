export function duration(from?: string, to?: string): string {
  if (!from || !to) return "";
  const s = Math.max(0, (Date.parse(to) - Date.parse(from)) / 1000);
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
  return `${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}m`;
}

export function relativeTime(iso: string, now = Date.now()): string {
  if (!iso) return "";
  const s = (now - Date.parse(iso)) / 1000;
  if (s < 90) return "just now";
  if (s < 5400) return `${Math.round(s / 60)} min ago`;
  if (s < 129600) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export const count = (n?: number) => (n ? n.toLocaleString() : "");

/** Rough, readable span for estimates: "45 s", "12 min", "1 h 20 min". */
export function roughDuration(seconds: number): string {
  if (seconds < 60) return `${Math.max(1, Math.round(seconds))} s`;
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest ? `${hours} h ${rest} min` : `${hours} h`;
}

/** "en-zh" -> "EN → ZH", "pt-BR>ja" -> "PT-BR → JA"; an unknown or missing direction shows as a dash. */
export function directionLabel(direction: string | undefined): string {
  const text = direction ?? "";
  const [from, to] = text.includes(">") ? text.split(">") : text.split("-");
  return from && to ? `${from.toUpperCase()} → ${to.toUpperCase()}` : "—";
}

/** The files a job can be made from, as the file picker's `accept` and as a test of a name. */
export const SOURCE_ACCEPT = ".epub,.rtf,.txt,.md,.markdown,.html,.htm,.xhtml,.docx";
export const isSourceBook = (name: string) => /\.(epub|rtf|txt|md|markdown|html?|xhtml|docx)$/i.test(name);
export const SOURCE_KINDS = "EPUB, RTF, text, Markdown, HTML, or Word (.docx)";

/** What a translated book can be downloaded as: the EPUB the pipeline builds, or its text in another format. */
export const OUTPUT_FORMATS: [string, string][] = [
  ["epub", "EPUB"],
  ["docx", "Word (.docx)"],
  ["html", "HTML"],
  ["md", "Markdown"],
  ["txt", "Plain text"],
];

/** Download URL of a completed job's translated book. */
export const outputUrl = (jobId: string, format = "epub") =>
  `/api/jobs/${encodeURIComponent(jobId)}/output${format === "epub" ? "" : `?format=${format}`}`;

/** Download URL of the whole book's current translation (edits included) as XLIFF 2.1. */
export const xliffExportUrl = (jobId: string) => `/api/jobs/${encodeURIComponent(jobId)}/text/export?format=xliff`;

/** Download URL of an XLIFF import's per-unit CSV report. */
export const importReportUrl = (jobId: string, importId: string) =>
  `/api/jobs/${encodeURIComponent(jobId)}/text/import/report?import_id=${encodeURIComponent(importId)}`;

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** Compact local time for a table cell: "14:05" today, "Sep 18 14:05" this year, else "2025-09-18 14:05". */
export function shortTimestamp(iso: string, now = new Date()): string {
  const at = new Date(iso);
  if (!iso || Number.isNaN(at.getTime())) return "";
  const pad = (n: number) => String(n).padStart(2, "0");
  const time = `${pad(at.getHours())}:${pad(at.getMinutes())}`;
  if (at.toDateString() === now.toDateString()) return time;
  if (at.getFullYear() === now.getFullYear()) return `${MONTHS[at.getMonth()]} ${at.getDate()} ${time}`;
  return `${at.getFullYear()}-${pad(at.getMonth() + 1)}-${pad(at.getDate())} ${time}`;
}
