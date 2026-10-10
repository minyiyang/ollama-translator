import { useT } from "../i18n";
import { roughDuration } from "../lib/format";
import { rerunWarning } from "../lib/serverText";
import { stageLabel } from "../lib/stages";
import { jobApi } from "../api";
import { useDialog } from "./Dialog";
import { useJob } from "./JobContext";
import { useToast } from "./Toast";

type RerunPreview = {
  stage: string;
  status: string;
  stages: { name: string; status: string; seconds: number | null }[];
  warnings: { code: string; message: string }[];
  previous_seconds: number | null;
};

/**
 * Ask before a stage is rerun: the dialog lists what is redone and what is
 * lost with it. Resolves true when the rerun is confirmed; a stage the server
 * refuses to rerun is said in a toast and resolves false. The Progress tab's
 * Rerun and the Config tab's "Save and rerun" both ask through this.
 */
export function useRerunDialog(): (stage: string) => Promise<boolean> {
  const t = useT();
  const { jobId, info } = useJob();
  const ask = useDialog();
  const toast = useToast();
  const type = info?.job_type;
  return async (stage: string) => {
    let preview: RerunPreview;
    try {
      preview = await jobApi<RerunPreview>(jobId, `rerun?stage=${encodeURIComponent(stage)}`);
    } catch (e) {
      toast("bad", (e as Error).message, 0);
      return false;
    }
    const stopped = preview.status !== "completed";
    const ran = preview.stages.filter((s) => s.status !== "pending");
    const later = preview.stages.length - ran.length;
    const choice = await ask(
      t("progress.rerun.title", { stage: stageLabel(stage, type) }),
      <>
        {stopped && <p>{t("progress.rerun.resumeKeeps")}</p>}
        <p>{t("progress.rerun.resets", { count: ran.length })}</p>
        <ol className="rerun-stages">
          {ran.map((s) => (
            <li key={s.name}>
              {stageLabel(s.name, type)}
              {s.seconds ? <span className="meta"> · {t("progress.rerun.took", { time: roughDuration(s.seconds) })}</span> : null}
            </li>
          ))}
        </ol>
        {later > 0 && <p className="meta">{t("progress.rerun.continues", { count: later })}</p>}
        {preview.previous_seconds ? <p className="meta">{t("progress.rerun.tookSoFar", { time: roughDuration(preview.previous_seconds) })}</p> : null}
        {preview.warnings.map((w) => <div key={w.code} className="banner warn">{rerunWarning(w)}</div>)}
      </>,
      [
        { value: "cancel", label: t("common.cancel"), primary: true },
        { value: "rerun", label: t("progress.rerun.confirm"), danger: true },
      ],
    );
    return choice === "rerun";
  };
}
