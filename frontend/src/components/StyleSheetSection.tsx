// The book style sheet on the Glossary tab (docs/BOOK_CONSISTENCY.md, phase 2):
// how characters are referred to and addressed, and recurring expressions with one
// fixed rendering. Reviewed and approved together with the glossary.

import { useT } from "../i18n";

export type StyleCharacter = {
  name: string;
  pronoun: string;
  addressed_as: string;
  voice: string;
  evidence: string[];
  alternatives: string[];
};
export type StyleExpression = { source: string; rendering: string; note: string; evidence: string[]; alternatives: string[] };
export type StyleConventions = { quotation_marks: string; nested_quotation_marks: string; ellipsis: string; dash: string; numerals: string };
export type StyleSheet = { characters: StyleCharacter[]; expressions: StyleExpression[]; conventions: StyleConventions };

export function StyleSheetSection({
  sheet,
  editable,
  edited,
  needsReview = false,
  pronouns,
  addresses,
  sourceLang = "en",
  targetLang = "zh-CN",
  onChange,
  onReset,
}: {
  // BCP 47 tags of the book's languages, for the cells' lang attributes.
  sourceLang?: string;
  targetLang?: string;
  sheet: StyleSheet;
  editable: boolean;
  edited: boolean;
  /** The gate is waiting for a person to review this sheet. */
  needsReview?: boolean;
  pronouns: string[];
  addresses: string[];
  onChange: (sheet: StyleSheet) => void;
  onReset: () => void;
}) {
  const t = useT();
  const setCharacter = (index: number, patch: Partial<StyleCharacter>) =>
    onChange({ ...sheet, characters: sheet.characters.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  const setExpression = (index: number, patch: Partial<StyleExpression>) =>
    onChange({ ...sheet, expressions: sheet.expressions.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  const conventions = sheet.conventions;

  return (
    <section className="card" aria-label={t("glossary.styleSheet.title")}>
      <div className="row" style={{ margin: "0 0 8px", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{t("glossary.styleSheet.title")}</h2>
        {editable && edited && <button className="small" onClick={onReset}>{t("glossary.styleSheet.discardEdits")}</button>}
      </div>
      {needsReview && (
        <div className="banner warn">{t("glossary.styleSheet.needsReview")}</div>
      )}
      <p className="meta">{t("glossary.styleSheet.intro")}</p>
      {sheet.characters.length === 0 && sheet.expressions.length === 0 && <p className="meta">{t("glossary.styleSheet.empty")}</p>}
      {sheet.characters.length > 0 && (
        <table className="grid">
          <thead><tr><th>{t("glossary.styleSheet.column.character")}</th><th>{t("glossary.styleSheet.column.pronoun")}</th><th>{t("glossary.styleSheet.column.addressedAs")}</th><th>{t("glossary.styleSheet.column.voice")}</th><th /></tr></thead>
          <tbody>
            {sheet.characters.map((item, index) => (
              <tr key={`${item.name}-${index}`}>
                <td>
                  <b>{item.name}</b>
                  {item.alternatives.length > 0 && <div className="meta">{t("glossary.styleSheet.alsoSuggested", { list: item.alternatives.join(", ") })}</div>}
                </td>
                <td>
                  {editable ? (
                    <select aria-label={t("glossary.styleSheet.pronounFor", { name: item.name })} value={item.pronoun} onChange={(e) => setCharacter(index, { pronoun: e.target.value })}>
                      <option value="">—</option>
                      {pronouns.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  ) : item.pronoun || "—"}
                </td>
                <td>
                  {editable ? (
                    <select aria-label={t("glossary.styleSheet.addressFor", { name: item.name })} value={item.addressed_as} onChange={(e) => setCharacter(index, { addressed_as: e.target.value })}>
                      <option value="">—</option>
                      {addresses.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  ) : item.addressed_as || "—"}
                </td>
                <td>
                  {editable ? (
                    <input type="text" aria-label={t("glossary.styleSheet.voiceOf", { name: item.name })} value={item.voice} onChange={(e) => setCharacter(index, { voice: e.target.value })} />
                  ) : item.voice}
                </td>
                <td>
                  {editable && (
                    <button className="small" onClick={() => onChange({ ...sheet, characters: sheet.characters.filter((_, i) => i !== index) })}>
                      {t("common.remove")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {sheet.expressions.length > 0 && (
        <table className="grid" style={{ marginTop: 12 }}>
          <thead><tr><th>{t("glossary.styleSheet.column.expression")}</th><th>{t("glossary.styleSheet.column.rendering")}</th><th /></tr></thead>
          <tbody>
            {sheet.expressions.map((item, index) => (
              <tr key={`${item.source}-${index}`}>
                <td lang={sourceLang}>{item.source}</td>
                <td lang={targetLang}>
                  {editable ? (
                    <input type="text" aria-label={t("glossary.styleSheet.renderingOf", { source: item.source })} value={item.rendering} onChange={(e) => setExpression(index, { rendering: e.target.value })} />
                  ) : item.rendering}
                  {item.alternatives.length > 0 && <div className="meta">{t("glossary.styleSheet.alsoSuggested", { list: item.alternatives.join(" / ") })}</div>}
                </td>
                <td>
                  {editable && (
                    <button className="small" onClick={() => onChange({ ...sheet, expressions: sheet.expressions.filter((_, i) => i !== index) })}>
                      {t("common.remove")}
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="meta" style={{ marginTop: 10 }}>
        {t("glossary.styleSheet.conventions", { quotes: conventions.quotation_marks, nested: conventions.nested_quotation_marks, ellipsis: conventions.ellipsis, dash: conventions.dash })}
      </p>
    </section>
  );
}
