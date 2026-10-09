// Curated view over AppConfig: the options most jobs change, with help text.
// Titles, labels, and help are message keys; the text is in the catalog (src/i18n).
// Every other field is still editable under "All settings" (schema-driven).

import { t, type MessageKey } from "../i18n";

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
  | { path: string; label: MessageKey; help?: MessageKey; model?: boolean }
  | { special: "glossary-review" | "language-pair" };

/** The settings a special option writes, for counts of changed and invalid settings. */
export const SPECIAL_PATHS: Record<"glossary-review" | "language-pair", string[]> = {
  "glossary-review": ["workflow.require_glossary_review", "workflow.llm_glossary_review"],
  "language-pair": ["translation.direction", "translation.source_language", "translation.target_language"],
};
export type CommonGroup = { title: MessageKey; help?: MessageKey; options: CommonOption[] };

export const COMMON_GROUPS: CommonGroup[] = [
  {
    title: "option.group.translation",
    options: [
      { special: "language-pair" },
      { path: "translation.style", label: "option.translation.style.label", help: "option.translation.style.help" },
      { path: "translation.custom_style_file", label: "option.translation.custom_style_file.label", help: "option.translation.custom_style_file.help" },
      { path: "translation.boundary_context", label: "option.translation.boundary_context.label", help: "option.translation.boundary_context.help" },
      {
        path: "translation.de_ai_enabled",
        label: "option.translation.de_ai_enabled.label",
        help: "option.translation.de_ai_enabled.help",
      },
      {
        path: "translation.de_ai_strength",
        label: "option.translation.de_ai_strength.label",
        help: "option.translation.de_ai_strength.help",
      },
      { path: "translation.thinking", label: "option.translation.thinking.label", help: "option.translation.thinking.help" },
    ],
  },
  {
    title: "option.group.models",
    help: "option.group.models.help",
    options: [
      { path: "ollama.model", label: "option.ollama.model.label", model: true, help: "option.ollama.model.help" },
      { path: "glossary.extraction_model", label: "option.glossary.extraction_model.label", model: true },
      { path: "audit.model", label: "option.audit.model.label", model: true, help: "option.audit.model.help" },
      { path: "audit.verifier_model", label: "option.audit.verifier_model.label", model: true, help: "option.audit.verifier_model.help" },
      { path: "audit.repair_model", label: "option.audit.repair_model.label", model: true, help: "option.audit.repair_model.help" },
      { path: "reprose.model", label: "option.reprose.model.label", model: true },
      { path: "reprose.verifier_model", label: "option.reprose.verifier_model.label", model: true },
      { path: "translation.model", label: "option.translation.model.label", model: true, help: "option.translation.model.help" },
      { path: "translation.fallback_models", label: "option.translation.fallback_models.label", model: true, help: "option.translation.fallback_models.help" },
    ],
  },
  {
    title: "option.group.glossary",
    options: [
      { special: "glossary-review" },
      { path: "glossary.extraction_enabled", label: "option.glossary.extraction_enabled.label", help: "option.glossary.extraction_enabled.help" },
      { path: "glossary.approval_auto_approve_min_confidence", label: "option.glossary.approval_auto_approve_min_confidence.label", help: "option.glossary.approval_auto_approve_min_confidence.help" },
      {
        path: "glossary.drop_generic_terms",
        label: "option.glossary.drop_generic_terms.label",
        help: "option.glossary.drop_generic_terms.help",
      },
      { path: "glossary.extraction_max_entries", label: "option.glossary.extraction_max_entries.label" },
      { path: "glossary.seed_glossaries", label: "option.glossary.seed_glossaries.label", help: "option.glossary.seed_glossaries.help" },
      { path: "glossary.series_glossaries", label: "option.glossary.series_glossaries.label", help: "option.glossary.series_glossaries.help" },
      { path: "glossary.book_glossaries", label: "option.glossary.book_glossaries.label", help: "option.glossary.book_glossaries.help" },
    ],
  },
  {
    title: "option.group.qualityChecks",
    options: [
      { path: "audit.semantic_enabled", label: "option.audit.semantic_enabled.label", help: "option.audit.semantic_enabled.help" },
      { path: "audit.repair_min_severity", label: "option.audit.repair_min_severity.label", help: "option.audit.repair_min_severity.help" },
      { path: "audit.quantity.enabled", label: "option.audit.quantity.enabled.label", help: "option.audit.quantity.enabled.help" },
      { path: "reprose.enabled", label: "option.reprose.enabled.label", help: "option.reprose.enabled.help" },
      { path: "reprose.candidate_mode", label: "option.reprose.candidate_mode.label", help: "option.reprose.candidate_mode.help" },
    ],
  },
  {
    title: "option.group.bookConsistency",
    help: "option.group.bookConsistency.help",
    options: [
      { path: "consistency.enabled", label: "option.consistency.enabled.label", help: "option.consistency.enabled.help" },
      { path: "consistency.quoted_speech", label: "option.consistency.quoted_speech.label", help: "option.consistency.quoted_speech.help" },
      { path: "consistency.conventions", label: "option.consistency.conventions.label", help: "option.consistency.conventions.help" },
      { path: "consistency.min_repeat_characters", label: "option.consistency.min_repeat_characters.label", help: "option.consistency.min_repeat_characters.help" },
      {
        path: "consistency.style_sheet.enabled",
        label: "option.consistency.style_sheet.enabled.label",
        help: "option.consistency.style_sheet.enabled.help",
      },
      {
        path: "consistency.story_context.enabled",
        label: "option.consistency.story_context.enabled.label",
        help: "option.consistency.story_context.enabled.help",
      },
      { path: "consistency.story_context.chapters_before", label: "option.consistency.story_context.chapters_before.label", help: "option.consistency.story_context.chapters_before.help" },
      {
        path: "consistency.style_sheet.review",
        label: "option.consistency.style_sheet.review.label",
        help: "option.consistency.style_sheet.review.help",
      },
      {
        path: "consistency.close_variant_similarity",
        label: "option.consistency.close_variant_similarity.label",
        help: "option.consistency.close_variant_similarity.help",
      },
    ],
  },
  {
    title: "option.group.reviewAndOutput",
    options: [
      { path: "workflow.require_final_review", label: "option.workflow.require_final_review.label", help: "option.workflow.require_final_review.help" },
      { path: "workflow.compile_max_unresolved_review_segments", label: "option.workflow.compile_max_unresolved_review_segments.label", help: "option.workflow.compile_max_unresolved_review_segments.help" },
      { path: "workflow.defer_failed_translation_segments", label: "option.workflow.defer_failed_translation_segments.label", help: "option.workflow.defer_failed_translation_segments.help" },
      { path: "epub.strip_print_page_markers", label: "option.epub.strip_print_page_markers.label" },
      { path: "epub.insert_missing_chapter_headings", label: "option.epub.insert_missing_chapter_headings.label", help: "option.epub.insert_missing_chapter_headings.help" },
      { path: "output.format", label: "option.output.format.label", help: "option.output.format.help" },
      { path: "output.pdf_font", label: "option.output.pdf_font.label", help: "option.output.pdf_font.help" },
    ],
  },
  {
    title: "option.group.bookTitle",
    options: [
      { path: "translation.translated_title", label: "option.translation.translated_title.label", help: "option.translation.translated_title.help" },
    ],
  },
  {
    title: "option.group.subtitles",
    help: "option.group.subtitles.help",
    options: [
      { path: "subtitles.line_characters", label: "option.subtitles.line_characters.label", help: "option.subtitles.line_characters.help" },
      { path: "subtitles.lines", label: "option.subtitles.lines.label", help: "option.subtitles.lines.help" },
      { path: "subtitles.characters_per_second", label: "option.subtitles.characters_per_second.label", help: "option.subtitles.characters_per_second.help" },
    ],
  },
  {
    title: "option.group.ollamaAndContext",
    options: [
      { path: "ollama.host", label: "option.ollama.host.label" },
      { path: "ollama.num_ctx", label: "option.ollama.num_ctx.label", help: "option.ollama.num_ctx.help" },
      { path: "translation.max_num_ctx", label: "option.translation.max_num_ctx.label" },
      { path: "ollama.temperature", label: "option.ollama.temperature.label", help: "option.ollama.temperature.help" },
      { path: "ollama.timeout_seconds", label: "option.ollama.timeout_seconds.label" },
      { path: "ollama.keep_alive", label: "option.ollama.keep_alive.label" },
    ],
  },
];

const OPTIONS = new Map(COMMON_GROUPS.flatMap((g) => g.options).flatMap((o) => ("path" in o ? [[o.path, o] as const] : [])));

/** The paths the curated view names. */
export const LABELLED_PATHS = [...OPTIONS.keys()];

/** An option's name in the interface language; undefined for one the curated view does not list. */
export const optionLabel = (path: string): string | undefined => {
  const option = OPTIONS.get(path);
  return option && t(option.label);
};

/** An option's help in the interface language; undefined when it has none. */
export const optionHelp = (path: string): string | undefined => {
  const help = OPTIONS.get(path)?.help;
  return help && t(help);
};

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
