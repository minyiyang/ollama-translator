// Curated view over AppConfig: the options most jobs change, with help text.
// Every other field is still editable under "All settings" (schema-driven).

export type SchemaField = {
  path: string;
  key: string;
  type: "boolean" | "integer" | "number" | "string" | "array" | "enum" | "object";
  default: any;
  nullable: boolean;
  enum?: string[];
  item_type?: string;
  value_type?: string;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
};
export type SchemaSection = { key: string; title: string; fields: SchemaField[] };

export type CommonOption =
  | { path: string; label: string; help?: string; model?: boolean }
  | { special: "glossary-review" | "language-pair" };

/** The settings a special option writes, for counts of changed and invalid settings. */
export const SPECIAL_PATHS: Record<"glossary-review" | "language-pair", string[]> = {
  "glossary-review": ["workflow.require_glossary_review", "workflow.llm_glossary_review"],
  "language-pair": ["translation.direction", "translation.source_language", "translation.target_language"],
};
export type CommonGroup = { title: string; help?: string; options: CommonOption[] };

export const COMMON_GROUPS: CommonGroup[] = [
  {
    title: "Translation",
    options: [
      { special: "language-pair" },
      { path: "translation.style", label: "Prose style", help: "Voice preset for the translation. “custom” uses the style file below." },
      { path: "translation.custom_style_file", label: "Custom style file", help: "Style instructions file, relative to this config. Used only with style “custom”." },
      { path: "translation.boundary_context", label: "Neighbor context", help: "Show the previous and next source segments read-only, for continuity across chunks." },
      {
        path: "translation.de_ai_enabled",
        label: "Naturalness guidance",
        help: "Adds anti-'AI prose' instructions to the translation, repair, and prose-rewrite prompts. Not a separate pass: no extra model calls.",
      },
      {
        path: "translation.de_ai_strength",
        label: "Naturalness strength",
        help: "conservative: keep the author's voice and avoid adding formulaic phrasing. moderate: actively avoid model-like prose (rhetorical triplets, uniform rhythm, over-explanation). For verified rewrites of stiff passages, turn on Prose rewrite.",
      },
      { path: "translation.thinking", label: "Model thinking", help: "Let the translation model reason before answering. Slower; off by default." },
    ],
  },
  {
    title: "Models",
    help: "Each model must be installed in Ollama. ✓ / ✗ shows what the configured host reports.",
    options: [
      { path: "ollama.model", label: "Primary model", model: true, help: "Translates, resolves and approves the glossary, and repairs unless overridden." },
      { path: "glossary.extraction_model", label: "Glossary extraction", model: true },
      { path: "audit.model", label: "Semantic audit", model: true, help: "Checks each translated passage against the source." },
      { path: "audit.verifier_model", label: "Repair verification", model: true, help: "Compares a proposed repair with the current translation." },
      { path: "audit.repair_model", label: "Targeted repair", model: true, help: "Leave empty to use the primary model." },
      { path: "reprose.model", label: "Prose rewrite", model: true },
      { path: "reprose.verifier_model", label: "Rewrite verification", model: true },
      { path: "translation.model", label: "Translation model", model: true, help: "Translates instead of the primary model, which keeps the glossary and review calls. Leave empty to translate with the primary model." },
      { path: "translation.fallback_models", label: "Fallback models", model: true, help: "Tried in order for passages the primary model could not translate." },
    ],
  },
  {
    title: "Glossary",
    options: [
      { special: "glossary-review" },
      { path: "glossary.extraction_enabled", label: "Extract terms from the book", help: "Turn off only when every glossary below is already reviewed." },
      { path: "glossary.approval_auto_approve_min_confidence", label: "Auto-approve confidence", help: "With LLM review, evidence-backed terms at or above this confidence skip the model." },
      {
        path: "glossary.drop_generic_terms",
        label: "Drop ordinary words",
        help: "With LLM or automatic approval, leave single ordinary words (“anchor”, “cheese”) out of the glossary so the translator words them by context. Human review decides on its own. Turn off if the book's invented words are lowercase.",
      },
      { path: "glossary.extraction_max_entries", label: "Max extracted terms" },
      { path: "glossary.seed_glossaries", label: "Seed glossaries", help: "Glossary files merged in before extraction (lowest precedence)." },
      { path: "glossary.series_glossaries", label: "Series glossaries", help: "Shared, reviewed terms for a book series." },
      { path: "glossary.book_glossaries", label: "Book glossaries", help: "Reviewed terms for this book (highest precedence)." },
    ],
  },
  {
    title: "Quality checks",
    options: [
      { path: "audit.semantic_enabled", label: "Semantic audit", help: "Model-check risky passages for meaning errors and repair them." },
      { path: "audit.repair_min_severity", label: "Repair threshold", help: "Lowest finding severity that triggers a repair." },
      { path: "audit.quantity.enabled", label: "Quantity audit", help: "Extra checks that numbers, units, and durations survived translation." },
      { path: "reprose.enabled", label: "Prose rewrite", help: "Propose more natural wording, verified against the source before it is kept." },
      { path: "reprose.candidate_mode", label: "Rewrite candidates", help: "risk-filtered rewrites only passages that read like translationese." },
    ],
  },
  {
    title: "Book consistency",
    help: "Book-wide checks with no model calls. Drift goes to repair; what repair cannot fix joins the review queue.",
    options: [
      { path: "consistency.enabled", label: "Consistency checks", help: "Find repeated lines, and repeated lines of dialogue, translated differently in different places." },
      { path: "consistency.quoted_speech", label: "Repeated dialogue", help: "Also compare quoted lines inside longer paragraphs, such as a character's catchphrase." },
      { path: "consistency.conventions", label: "Punctuation conventions", help: "Flag a single — where the book uses ——, and straight \" in Chinese text." },
      { path: "consistency.min_repeat_characters", label: "Shortest repeat", help: "Lines shorter than this are ignored, so “Yes.” or “Oh!” may vary." },
      {
        path: "consistency.style_sheet.enabled",
        label: "Book style sheet",
        help: "Also extract, with the glossary, lines the book repeats (rendered the same way throughout) and notes on each character. Character notes are context only: the source wording and the scene decide pronouns and 你/您.",
      },
      {
        path: "consistency.story_context.enabled",
        label: "Story context",
        help: "Summarize each chapter first (about 6 s per chapter) and give each translation chunk the story so far: the previous chapters and its own. Context only; the source still decides.",
      },
      { path: "consistency.story_context.chapters_before", label: "Earlier chapters in context", help: "How many previous chapter summaries each chunk sees." },
      {
        path: "consistency.style_sheet.review",
        label: "Style sheet review",
        help: "human: the glossary gate waits for you to review the style sheet on the Glossary tab, even when the glossary itself is LLM-reviewed. glossary: follow the glossary's review setting.",
      },
      {
        path: "consistency.close_variant_similarity",
        label: "Drift threshold",
        help: "How similar two renderings must be to count as drift (repaired). Less similar wording is treated as intentional and only listed.",
      },
    ],
  },
  {
    title: "Review and output",
    options: [
      { path: "workflow.require_final_review", label: "Always require final approval", help: "Pause before compiling even when nothing is left in the review queue." },
      { path: "workflow.compile_max_unresolved_review_segments", label: "Allowed unresolved segments", help: "Compile still pauses when more flagged segments than this remain." },
      { path: "workflow.defer_failed_translation_segments", label: "Defer failed passages", help: "Keep going when one passage fails validation; it goes to repair instead of stopping the run." },
      { path: "epub.strip_print_page_markers", label: "Remove print page markers" },
      { path: "epub.insert_missing_chapter_headings", label: "Add missing chapter headings", help: "Uses the book's table of contents for chapters without a visible heading." },
    ],
  },
  {
    title: "Ollama and context",
    options: [
      { path: "ollama.host", label: "Ollama host" },
      { path: "ollama.num_ctx", label: "Context ceiling", help: "Largest context any request may use; requests start small and grow only as needed." },
      { path: "translation.max_num_ctx", label: "Translation context limit" },
      { path: "ollama.temperature", label: "Temperature", help: "0 keeps output deterministic and resumable." },
      { path: "ollama.timeout_seconds", label: "Request timeout (s)" },
      { path: "ollama.keep_alive", label: "Keep model loaded" },
    ],
  },
];

export const HELP: Record<string, string> = Object.fromEntries(
  COMMON_GROUPS.flatMap((g) => g.options).flatMap((o) => ("path" in o && o.help ? [[o.path, o.help]] : [])),
);

export const LABELS: Record<string, string> = Object.fromEntries(
  COMMON_GROUPS.flatMap((g) => g.options).flatMap((o) => ("path" in o ? [[o.path, o.label]] : [])),
);

export const MODEL_PATHS = new Set(
  COMMON_GROUPS.flatMap((g) => g.options).flatMap((o) => ("path" in o && o.model ? [o.path] : [])),
);
MODEL_PATHS.add("audit.quantity.model");
MODEL_PATHS.add("audit.quantity.escalation_model");

/** "max_num_ctx" -> "Max num ctx" */
export const humanize = (key: string) => {
  const text = key.replace(/_/g, " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
};

export type GlossaryReviewMode = "llm" | "human" | "none";

export function glossaryReviewMode(require: boolean, llm: boolean): GlossaryReviewMode {
  return llm ? "llm" : require ? "human" : "none";
}

export const GLOSSARY_REVIEW_FLAGS: Record<GlossaryReviewMode, { require: boolean; llm: boolean }> = {
  llm: { require: true, llm: true },
  human: { require: true, llm: false },
  none: { require: false, llm: false },
};
