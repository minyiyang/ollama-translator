import { api } from "../api";
import { t } from "../i18n";

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

/** The pair string for two codes, in the spelling the server stores. */
export function pairOf(source: string, target: string): string {
  const s = source.trim();
  const t = target.trim();
  if ((s === "en" && t === "zh") || (s === "zh" && t === "en")) return `${s}-${t}`;
  return `${s}>${t}`;
}

/** An HTML lang value for a language code; `zh` is Simplified Chinese. */
export function langAttr(code: string): string {
  return code === "zh" ? "zh-CN" : code;
}

export type Tier = "tuned" | "profiled" | "generic";
export type Language = { code: string; name: string; tier: Tier };
export type SkippedCheck = { check: string; reason: string };
export type LanguageSupport = {
  pair: string;
  source: Language;
  target: Language;
  skipped: SkippedCheck[];
  /** The model-quality warning for a pair beyond the tuned languages; "" otherwise. */
  notice: string;
};

const TIER_HELP = { tuned: "format.tier.tuned", profiled: "format.tier.profiled", generic: "format.tier.generic" } as const;
export const tierHelp = (tier: Tier) => t(TIER_HELP[tier]);

/** Whether a pair goes beyond the tuned languages (and so deserves a notice). */
export const isGeneric = (support: LanguageSupport | null | undefined) =>
  !!support && (support.source.tier !== "tuned" || support.target.tier !== "tuned");

/** Options that do nothing when their check is skipped for the pair (hidden in the
 *  Options view, with a line saying why). Keys are the server's check names. */
export const SKIP_HIDES: Record<string, string[]> = {
  "prose rewrite": ["reprose.enabled", "reprose.candidate_mode", "reprose.model", "reprose.verifier_model"],
  "punctuation conventions": ["consistency.conventions"],
};

export function hiddenOptions(support: LanguageSupport | null | undefined): Map<string, SkippedCheck> {
  const hidden = new Map<string, SkippedCheck>();
  for (const skipped of support?.skipped ?? []) {
    for (const path of SKIP_HIDES[skipped.check] ?? []) hidden.set(path, skipped);
  }
  return hidden;
}

type LanguagesPayload = { languages: Language[]; support: LanguageSupport | null; error: string };

const cache = new Map<string, Promise<LanguagesPayload>>();

/** The language list, plus what `pair` supports (or why it is not a pair). Cached per pair. */
export function loadLanguages(pair = ""): Promise<LanguagesPayload> {
  let request = cache.get(pair);
  if (!request) {
    request = api<LanguagesPayload>(`/api/languages?pair=${encodeURIComponent(pair)}`);
    request.catch(() => cache.delete(pair));
    cache.set(pair, request);
  }
  return request;
}

// Unicode scripts behind the ISO 15924 codes that name more than one.
const SCRIPT_PARTS: Record<string, string[]> = {
  Hans: ["Hani"],
  Hant: ["Hani"],
  Jpan: ["Hani", "Hira", "Kana"],
  Kore: ["Hang", "Hani"],
};

/** The Unicode scripts a language is written in ("en" -> ["Latn"]); empty when unknown. */
export function scriptsOf(code: string): string[] {
  try {
    const script = new Intl.Locale(code).maximize().script ?? "";
    return SCRIPT_PARTS[script] ?? (script ? [script] : []);
  } catch {
    return []; // not a language code
  }
}

/**
 * Runs of two or more letters in a script the source language uses and the
 * target does not: source text left untranslated. Empty for a pair that shares
 * its script (en>de), where leftover source text cannot be told apart.
 */
export function leftoverSourceText(text: string, source: string, target: string): string[] {
  const used = scriptsOf(target);
  const foreign = scriptsOf(source).filter((script) => !used.includes(script));
  if (!foreign.length || !used.length) return [];
  try {
    const run = new RegExp(`[${foreign.map((script) => `\\p{Script=${script}}`).join("")}]{2,}`, "gu");
    return [...new Set(text.match(run) ?? [])];
  } catch {
    return []; // a script this browser's Unicode data does not know
  }
}
