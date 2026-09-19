import { stageLabel } from "../lib/stages";
import { Chip } from "./ui";

export type Check = {
  ok: boolean;
  problems: string[];
  job_id: string;
  models: { role: string; model: string; installed: boolean | null }[];
  summary: Record<string, string | boolean>;
  stages: string[];
};

export function CheckResult({ check }: { check: Check }) {
  const s = check.summary;
  const onOff = (v: unknown) => (v ? "on" : "off");
  return (
    <>
      {check.ok ? (
        <div className="banner ok">Validated. Use <b>Start translation</b> at the top to run <span className="mono">{check.job_id}</span>.</div>
      ) : (
        <div className="banner bad">Not ready yet:<ul className="problems">{check.problems.map((p) => <li key={p}>{p}</li>)}</ul></div>
      )}
      <h2 className="label">Settings</h2>
      <div className="row" style={{ marginBottom: 14 }}>
        <Chip>{String(s.direction)}</Chip>
        <Chip>style: {String(s.style)}</Chip>
        <Chip>glossary review: {String(s.glossary_review)}</Chip>
        <Chip>semantic audit: {onOff(s.semantic_audit)}</Chip>
        <Chip>prose rewrite: {onOff(s.reprose)}</Chip>
        <Chip>final approval: {s.final_review_required ? "always" : "when queue is not empty"}</Chip>
      </div>
      <h2 className="label">Models</h2>
      <table className="grid" style={{ marginBottom: 14 }}>
        <thead><tr><th>Role</th><th>Model</th><th>Installed</th></tr></thead>
        <tbody>
          {check.models.map((m) => (
            <tr key={m.role}>
              <td>{m.role}</td>
              <td className="mono">{m.model}</td>
              <td>{m.installed === null ? <span className="meta">unknown</span> : m.installed ? <span className="ok-mark">✓</span> : <span className="bad-mark">✗ missing</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      <h2 className="label">Pipeline</h2>
      <div className="pipeline">{check.stages.map((st) => <Chip key={st}>{stageLabel(st)}</Chip>)}</div>
    </>
  );
}
