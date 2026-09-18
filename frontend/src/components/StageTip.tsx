import { useId, useState, type ReactNode } from "react";
import { STAGE_INFO } from "../lib/stageInfo";

/**
 * ⓘ next to a stage name: what the stage does, its input/output, what it checks,
 * the model it uses, and the live result. Opens on hover or keyboard focus;
 * clicking pins it open until the next click.
 */
export function StageTip({ stage, result }: { stage: string; result: ReactNode }) {
  const info = STAGE_INFO[stage];
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
        aria-label={`About this stage`}
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
            <dt>Input</dt><dd>{info.input}</dd>
            <dt>Output</dt><dd>{info.output}</dd>
            <dt>Checks</dt><dd>{info.checks}</dd>
            {info.model && (<><dt>Model</dt><dd className="mono">{info.model}</dd></>)}
            {info.pauses && (<><dt>Pauses</dt><dd>{info.pauses}</dd></>)}
            <dt>Result</dt><dd>{result}</dd>
          </dl>
        </div>
      )}
    </span>
  );
}
