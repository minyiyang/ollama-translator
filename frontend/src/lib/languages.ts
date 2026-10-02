import { api } from "../api";

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

export const TIER_HELP: Record<Tier, string> = {
  tuned: "Full profile, benchmarked, prompts with examples.",
  profiled: "Profile filled in; its checks run.",
  generic: "No profile yet: universal checks only, so more passages land in human review.",
};

/** Whether a pair goes beyond the tuned languages (and so deserves a notice). */
export const isGeneric = (support: LanguageSupport | null | undefined) =>
  !!support && (support.source.tier !== "tuned" || support.target.tier !== "tuned");

/** Options that do nothing when their check is skipped for the pair (hidden in the
 *  Options view, with a line saying why). Keys are the server's check names. */
export const SKIP_HIDES: Record<string, string[]> = {
  "prose rewrite": ["reprose.enabled", "reprose.candidate_mode", "reprose.model", "reprose.verifier_model"],
  "punctuation conventions and character report": ["consistency.conventions"],
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
