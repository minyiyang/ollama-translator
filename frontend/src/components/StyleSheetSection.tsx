// The book style sheet on the Glossary tab (docs/BOOK_CONSISTENCY.md, phase 2):
// how characters are referred to and addressed, and recurring expressions with one
// fixed rendering. Reviewed and approved together with the glossary.

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
  onChange,
  onReset,
}: {
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
  const setCharacter = (index: number, patch: Partial<StyleCharacter>) =>
    onChange({ ...sheet, characters: sheet.characters.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  const setExpression = (index: number, patch: Partial<StyleExpression>) =>
    onChange({ ...sheet, expressions: sheet.expressions.map((item, i) => (i === index ? { ...item, ...patch } : item)) });
  const conventions = sheet.conventions;

  return (
    <section className="card" aria-label="Style sheet">
      <div className="row" style={{ margin: "0 0 8px", justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>Style sheet</h2>
        {editable && edited && <button className="small" onClick={onReset}>Discard style-sheet edits</button>}
      </div>
      {needsReview && (
        <div className="banner warn">
          Waiting for your review. Check each entry, then approve with the glossary below: the style sheet is approved as
          shown here, whichever approve button you use.
        </div>
      )}
      <p className="meta">
        Character notes are context only: the source wording and the scene decide pronouns and forms of address (你/您),
        and translation uses a note only where the source leaves the choice open. Recurring expressions are rendered the
        same way wherever the line recurs with the same meaning.
      </p>
      {sheet.characters.length === 0 && sheet.expressions.length === 0 && <p className="meta">No entries were found.</p>}
      {sheet.characters.length > 0 && (
        <table className="grid">
          <thead><tr><th>Character</th><th>Pronoun</th><th>Addressed as</th><th>Voice</th><th /></tr></thead>
          <tbody>
            {sheet.characters.map((item, index) => (
              <tr key={`${item.name}-${index}`}>
                <td>
                  <b>{item.name}</b>
                  {item.alternatives.length > 0 && <div className="meta">also suggested: {item.alternatives.join(", ")}</div>}
                </td>
                <td>
                  {editable ? (
                    <select aria-label={`Pronoun for ${item.name}`} value={item.pronoun} onChange={(e) => setCharacter(index, { pronoun: e.target.value })}>
                      <option value="">—</option>
                      {pronouns.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  ) : item.pronoun || "—"}
                </td>
                <td>
                  {editable ? (
                    <select aria-label={`Address for ${item.name}`} value={item.addressed_as} onChange={(e) => setCharacter(index, { addressed_as: e.target.value })}>
                      <option value="">—</option>
                      {addresses.map((value) => <option key={value} value={value}>{value}</option>)}
                    </select>
                  ) : item.addressed_as || "—"}
                </td>
                <td>
                  {editable ? (
                    <input type="text" aria-label={`Voice of ${item.name}`} value={item.voice} onChange={(e) => setCharacter(index, { voice: e.target.value })} />
                  ) : item.voice}
                </td>
                <td>
                  {editable && (
                    <button className="small" onClick={() => onChange({ ...sheet, characters: sheet.characters.filter((_, i) => i !== index) })}>
                      Remove
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
          <thead><tr><th>Recurring expression</th><th>Rendering</th><th /></tr></thead>
          <tbody>
            {sheet.expressions.map((item, index) => (
              <tr key={`${item.source}-${index}`}>
                <td lang="en">{item.source}</td>
                <td lang="zh-CN">
                  {editable ? (
                    <input type="text" aria-label={`Rendering of ${item.source}`} value={item.rendering} onChange={(e) => setExpression(index, { rendering: e.target.value })} />
                  ) : item.rendering}
                  {item.alternatives.length > 0 && <div className="meta">also suggested: {item.alternatives.join(" / ")}</div>}
                </td>
                <td>
                  {editable && (
                    <button className="small" onClick={() => onChange({ ...sheet, expressions: sheet.expressions.filter((_, i) => i !== index) })}>
                      Remove
                    </button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="meta" style={{ marginTop: 10 }}>
        Conventions: quotation marks {conventions.quotation_marks}, nested {conventions.nested_quotation_marks}, ellipsis {conventions.ellipsis}, dash {conventions.dash}
      </p>
    </section>
  );
}
