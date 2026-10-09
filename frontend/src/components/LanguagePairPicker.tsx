import { useEffect, useState } from "react";
import { useT, type MessageKey } from "../i18n";
import { tierLabel } from "../lib/enums";
import {
  tierHelp,
  isGeneric,
  loadLanguages,
  pairCodes,
  pairOf,
  type Language,
  type LanguageSupport,
} from "../lib/languages";
import { Chip } from "./ui";

/** What a pair supports: tiers, the model notice, and the checks its languages cannot run. */
export function LanguageNotes({ support, open = false }: { support: LanguageSupport; open?: boolean }) {
  const t = useT();
  return (
    <div className="language-notes">
      <div className="row" style={{ margin: 0 }}>
        <span>{t("jobs.languages.pair", { source: support.source.name, target: support.target.name })}</span>
        {[support.source, support.target].map((side, i) => (
          <Chip key={i} kind={side.tier === "tuned" ? "ok" : "warn"} title={tierHelp(side.tier)}>
            {side.code}: {tierLabel(side.tier)}
          </Chip>
        ))}
      </div>
      {support.notice && <div className="banner warn">{support.notice}</div>}
      {support.skipped.length > 0 && (
        <details open={open}>
          <summary className="meta">{t("jobs.languages.checksSkipped", { count: support.skipped.length })}</summary>
          <ul className="skipped-checks">
            {support.skipped.map((item) => (
              <li key={item.check}><b>{item.check}</b>: <span className="meta">{item.reason}</span></li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

/** The source and target language of a job or series. Any BCP 47 code is accepted;
 *  the list offers the profiled languages first. Writes "en-zh"/"zh-en" or "src>tgt". */
export function LanguagePairPicker({
  value,
  onChange,
  disabled = false,
  showNotes = true,
}: {
  value: string;
  onChange: (pair: string) => void;
  disabled?: boolean;
  showNotes?: boolean;
}) {
  const t = useT();
  const [codes, setCodes] = useState<[string, string]>(() => pairCodes(value));
  const [languages, setLanguages] = useState<Language[]>([]);
  const [support, setSupport] = useState<LanguageSupport | null>(null);
  const [error, setError] = useState("");

  useEffect(() => setCodes(pairCodes(value)), [value]);
  useEffect(() => {
    let current = true;
    loadLanguages(value)
      .then((payload) => {
        if (!current) return;
        setLanguages(payload.languages);
        setSupport(payload.support);
        setError(payload.error);
      })
      .catch((e: Error) => current && setError(e.message));
    return () => { current = false; };
  }, [value]);

  const known = new Set(languages.map((language) => language.code));
  const commit = (next: [string, string]) => {
    setCodes(next);
    if (next[0].trim() && next[1].trim()) {
      const pair = pairOf(next[0], next[1]);
      if (pair !== value) onChange(pair);
    }
  };
  // A code picked from the list is committed at once; a typed one on Enter or blur.
  const edit = (side: 0 | 1, code: string) => {
    const next: [string, string] = side === 0 ? [code, codes[1]] : [codes[0], code];
    if (known.has(code.trim())) commit(next);
    else setCodes(next);
  };
  const nameOf = (code: string) => languages.find((language) => language.code === code)?.name ?? "";

  const input = (side: 0 | 1, label: MessageKey, name: MessageKey) => (
    <label className="pair-side">
      <span className="meta">{t(label)}</span>
      <input
        type="text"
        list="language-codes"
        aria-label={t(name)}
        value={codes[side]}
        disabled={disabled}
        placeholder={side === 0 ? "en" : "ja"}
        onChange={(e) => edit(side, e.target.value)}
        onBlur={() => commit(codes)}
        onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); commit(codes); } }}
      />
      <span className="meta">{nameOf(codes[side]) || " "}</span>
    </label>
  );

  return (
    <div className="pair-picker">
      <datalist id="language-codes">
        {languages.map((language) => (
          <option key={language.code} value={language.code} label={`${language.name}${language.tier === "generic" ? "" : ` · ${tierLabel(language.tier)}`}`} />
        ))}
      </datalist>
      <div className="row" style={{ margin: 0, alignItems: "flex-start" }}>
        {input(0, "jobs.languages.from", "jobs.languages.fromLabel")}
        <button type="button" className="small" disabled={disabled} title={t("jobs.languages.swap")} aria-label={t("jobs.languages.swap")}
          onClick={() => commit([codes[1], codes[0]])}>⇄</button>
        {input(1, "jobs.languages.into", "jobs.languages.intoLabel")}
      </div>
      {error && <div className="error">{error}</div>}
      {showNotes && support && !error && (isGeneric(support) || support.skipped.length > 0) && <LanguageNotes support={support} />}
    </div>
  );
}
