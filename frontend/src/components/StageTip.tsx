import { useId, useState, type ReactNode } from "react";
import { useT } from "../i18n";
import { stageInfo } from "../lib/stageInfo";

/**
 * ⓘ next to a stage name: what the stage does, its input/output, what it checks,
 * the model it uses, and the live result. Opens on hover or keyboard focus;
 * clicking pins it open until the next click.
 */
export function StageTip({ stage, result, jobType }: { stage: string; result: ReactNode; jobType?: string }) {
  const t = useT();
  const info = stageInfo(stage, jobType);
  const id = useId();
  const [hover, setHover] = useState(false);
  const [pinned, setPinned] = useState(false);
  if (!info) return null;
  const open = hover || pinned;
  return (
    <span className="stage-tip" onMouseEnter={() => setHover(true)} onMouseLeave={() => setHover(false)}>
      <button
        type="button"
        className="tip-icon"
        aria-label={t("progress.tip.about")}
        aria-describedby={open ? id : undefined}
        aria-expanded={open}
        onFocus={() => setHover(true)}
        onBlur={() => { setHover(false); setPinned(false); }}
        onClick={() => setPinned((p) => !p)}
      >
        ⓘ
      </button>
      {open && (
        <div className="tip-card" role="tooltip" id={id}>
          <p className="tip-does">{info.does}</p>
          <dl>
            <dt>{t("progress.tip.input")}</dt><dd>{info.input}</dd>
            <dt>{t("progress.tip.output")}</dt><dd>{info.output}</dd>
            <dt>{t("progress.tip.checks")}</dt><dd>{info.checks}</dd>
            {info.model && (<><dt>{t("progress.tip.model")}</dt><dd className="mono">{info.model}</dd></>)}
            {info.pauses && (<><dt>{t("progress.tip.pauses")}</dt><dd>{info.pauses}</dd></>)}
            <dt>{t("progress.tip.result")}</dt><dd>{result}</dd>
          </dl>
        </div>
      )}
    </span>
  );
}
