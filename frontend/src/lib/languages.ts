/** A glossary's language pair as the API reports it: its code and each side's language name. */
export type GlossaryPair = { pair: string; source: string; target: string };

/** Labels for a glossary whose pair is unknown (an older server). */
export const FALLBACK_PAIR: GlossaryPair = { pair: "en-zh", source: "English", target: "Chinese" };

/** The source and target language codes of a pair: "en-zh" and "zh-en" use a hyphen
 *  (they predate the others), any other pair an arrow ("en>ja", "pt-BR>fr"). */
export function pairCodes(pair: string): [string, string] {
  if (pair === "en-zh" || pair === "zh-en") return pair.split("-") as [string, string];
  const [source = "", target = ""] = pair.split(">");
  return [source, target];
}

/** An HTML lang value for a language code; `zh` is Simplified Chinese. */
export function langAttr(code: string): string {
  return code === "zh" ? "zh-CN" : code;
}
