import { rich, useT } from "../i18n";
import { glossaryReviewLabel, roleLabel, styleLabel } from "../lib/enums";
import { directionLabel } from "../lib/format";
import { isGeneric, type LanguageSupport } from "../lib/languages";
import { stageLabel } from "../lib/stages";
import { LanguageNotes } from "./LanguagePairPicker";
import { Chip } from "./ui";

export type Check = {
  ok: boolean;
  problems: string[];
  job_id: string;
  models: { role: string; model: string; installed: boolean | null }[];
  summary: Record<string, string | boolean> & { languages?: LanguageSupport };
  stages: string[];
};

export function CheckResult({ check }: { check: Check }) {
  const t = useT();
  const s = check.summary;
  return (
    <>
      {check.ok ? (
        <div className="banner ok">{rich("glossary.check.validated", { b: (chunks) => <b>{chunks}</b>, job: <span className="mono">{check.job_id}</span> })}</div>
      ) : (
        <div className="banner bad">{t("glossary.check.notReady")}<ul className="problems">{check.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
      )}
      <h2 className="label">{t("glossary.check.settings")}</h2>
      <div className="row" style={{ marginBottom: 14 }}>
        <Chip title={String(s.direction)}>{directionLabel(String(s.direction))}</Chip>
        <Chip>{t("glossary.check.style", { style: styleLabel(String(s.style)) })}</Chip>
        <Chip>{t("glossary.check.glossaryReview", { mode: glossaryReviewLabel(String(s.glossary_review)) })}</Chip>
        <Chip>{t("glossary.check.semanticAudit", { on: String(!!s.semantic_audit) })}</Chip>
        <Chip>{t("glossary.check.proseRewrite", { on: String(!!s.reprose) })}</Chip>
        <Chip>{t("glossary.check.finalApproval", { always: String(!!s.final_review_required) })}</Chip>
      </div>
      {s.languages && (isGeneric(s.languages) || s.languages.skipped.length > 0) && (
        <div style={{ marginBottom: 14 }}><LanguageNotes support={s.languages} open={isGeneric(s.languages)} /></div>
      )}
      <h2 className="label">{t("glossary.check.models")}</h2>
      <table className="grid" style={{ marginBottom: 14 }}>
        <thead><tr><th>{t("glossary.check.column.role")}</th><th>{t("glossary.check.column.model")}</th><th>{t("glossary.check.column.installed")}</th></tr></thead>
        <tbody>
          {check.models.map((m) => (
            <tr key={m.role}>
              <td>{roleLabel(m.role)}</td>
              <td className="mono">{m.model}</td>
              <td>{m.installed === null ? <span className="meta">{t("glossary.check.unknown")}</span> : m.installed ? <span className="ok-mark">✓</span> : <span className="bad-mark">{t("glossary.check.missing")}</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 className="label">{t("glossary.check.pipeline")}</h2>
      <div className="pipeline">{check.stages.map((st) => <Chip key={st}>{stageLabel(st)}</Chip>)}</div>
    </>
  );
}
