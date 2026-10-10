import { useT, type MessageKey } from "../i18n";
import { optionLabel, type JobKind } from "../lib/configCatalog";
import { shortTimestamp } from "../lib/format";
import { stageLabel } from "../lib/stages";

/** One changed setting, as the server describes it (book_agent/config_impact.py). */
export type ConfigChange = {
  path: string;
  before: unknown;
  after: unknown;
  /** The first stage that reads the setting; "" when nothing already done depends on it. */
  stage: string;
  /** Whether that stage has finished: its result does not have the change until it is rerun. */
  finished: boolean;
  /** Why the setting cannot be changed once a job has started; "" when it can. */
  locked: string;
};
export type ConfigPreview = {
  errors: { path: string; message: string }[];
  changes: ConfigChange[];
  locked: string[];
  /** The earliest finished stage a change affects: the job is rerun from it. "" when none is. */
  rerun_stage: string;
};
export type SavedChange = { at: string; changes: { path: string; before: unknown; after: unknown; stage?: string }[] };

/** A setting's value as a person would write it in the file. */
const shown = (value: unknown): string =>
  value === null || value === undefined ? "—" : typeof value === "string" ? value || '""' : JSON.stringify(value);

/** Why a setting stays locked once a job has started. */
export const lockedReason = (reason: string, t: (key: MessageKey) => string): string =>
  t(reason === "paths" ? "config.locked.paths" : "config.locked.direction");

/** What a change does to the job, in one sentence. */
function Effect({ change, jobType }: { change: ConfigChange; jobType?: JobKind }) {
  const t = useT();
  if (change.locked) return <span className="bad-mark">{lockedReason(change.locked, t)}</span>;
  if (!change.stage) return <>{t("config.effect.next")}</>;
  const stage = stageLabel(change.stage, jobType);
  if (!change.finished) return <>{t("config.effect.pending", { stage })}</>;
  return <>{t("config.effect.firstStage", { stage })} {t(`config.effect.${change.stage}` as MessageKey)}</>;
}

/**
 * What a proposed config changes in a started job: each setting with its old
 * and new value and what the change does to the pipeline.
 */
export function ConfigChangeList({ preview, jobType }: { preview: ConfigPreview; jobType?: JobKind }) {
  const t = useT();
  if (preview.errors.length) {
    return (
      <div className="banner bad">
        {t("config.changes.problems")}
        {preview.errors.map((error, index) => (
          <div key={index}>{error.path ? <b className="mono">{error.path}: </b> : null}{error.message}</div>
        ))}
      </div>
    );
  }
  if (!preview.changes.length) return <p className="meta">{t("config.changes.none")}</p>;
  return (
    <ul className="config-changes">
      {preview.changes.map((change) => (
        <li key={change.path} className={change.locked ? "locked" : ""}>
          <div className="name">{optionLabel(change.path, jobType) ?? change.path}</div>
          <div className="path">{change.path}</div>
          <div className="values"><span className="mono">{shown(change.before)}</span> → <span className="mono">{shown(change.after)}</span></div>
          <div className="help"><Effect change={change} jobType={jobType} /></div>
        </li>
      ))}
    </ul>
  );
}

/** The changes saved to this job's config before, newest first: a result can be traced to the config that made it. */
export function ConfigHistory({ history }: { history: SavedChange[] }) {
  const t = useT();
  if (!history.length) return null;
  return (
    <details className="config-history">
      <summary>{t("config.history.title", { count: history.length })}</summary>
      <ul>
        {[...history].reverse().map((entry, index) => (
          <li key={index}>
            <span className="meta">{shortTimestamp(entry.at)}</span>{" "}
            {entry.changes.map((change) => (
              <div key={change.path}><span className="mono">{change.path}</span>: <span className="mono">{shown(change.before)}</span> → <span className="mono">{shown(change.after)}</span></div>
            ))}
          </li>
        ))}
      </ul>
    </details>
  );
}
