import { useState } from "react";
import { jobKey, useT, type MessageKey } from "../i18n";
import { outputFormatLabel, outputFormats, outputUrl, type JobType } from "../lib/format";
import { useToast } from "./Toast";

/** What the server says of a finished job's output: its own format, and the others it can be had in. */
export type OutputChoice = {
  job_type?: JobType;
  output_format?: string;
  output_formats?: string[];
  /** For a subtitle job: what each other format does not carry over, as codes (subtitles.note.*). */
  output_notes?: Record<string, string[]>;
};

/**
 * A finished job's Download link, with a menu of the formats it can be had in
 * to its left. The menu starts on the format the job gives back; a subtitle
 * job's lists subtitle formats, a book's the book formats.
 */
export function DownloadControl({ jobId, output, small = false }: { jobId: string; output: OutputChoice; small?: boolean }) {
  const t = useT();
  const toast = useToast();
  // The format picked here; until one is, the one the job gives back.
  const [picked, setPicked] = useState<string | null>(null);
  const subtitles = output.job_type === "subtitles";
  const given = output.output_format ?? "epub";
  const format = picked ?? given;
  // A server from before subtitle jobs had formats names none: the one file is all there is.
  const formats = outputFormats(subtitles ? output.output_formats ?? [] : output.output_formats);
  // A subtitle file in another format than its own: what that format does not hold is said as it is downloaded.
  const lost = (output.output_notes?.[format] ?? []).map((code) => t(`subtitles.note.${code}` as MessageKey));
  const sayWhatIsLost = () => {
    if (lost.length) toast("info", t("jobs.controls.converted", { format: outputFormatLabel(format), notes: lost.join("; ") }), 0);
  };
  return (
    <>
      {formats.length > 1 && (
        <select className="small" aria-label={t("jobs.controls.downloadFormat")} value={format} onChange={(e) => setPicked(e.target.value)}
          title={t(jobKey("jobs.controls.downloadFormatTip", output.job_type))}>
          {formats.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
        </select>
      )}
      <a className={small ? "button small" : "button primary"} href={outputUrl(jobId, format === given ? "" : format)} download onClick={sayWhatIsLost}
        title={t(subtitles ? "jobs.downloadSubtitles" : "jobs.downloadBook")}>{t("jobs.download")}</a>
    </>
  );
}
