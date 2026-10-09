// Plain-language description of each pipeline stage, shown as a tooltip on the
// Progress tab. The text is in the catalog (src/i18n); keep the English in sync
// with docs/DESIGN.md (§4 workflow, §9 validation).

import { jobKey, t, type MessageKey } from "../i18n";

export type StageInfo = {
  does: string;
  input: string;
  output: string;
  checks: string;
  model?: string; // config key naming the model, when the stage calls one
  pauses?: string;
};

const STAGE_INFO: Record<string, { [Part in keyof StageInfo]: MessageKey }> = {
  decompile: {
    does: "stage.decompile.does",
    input: "stage.decompile.input",
    output: "stage.decompile.output",
    checks: "stage.decompile.checks",
  },
  extract_glossary: {
    does: "stage.extract_glossary.does",
    input: "stage.extract_glossary.input",
    output: "stage.extract_glossary.output",
    checks: "stage.extract_glossary.checks",
    model: "stage.extract_glossary.model",
  },
  resolve_glossary: {
    does: "stage.resolve_glossary.does",
    input: "stage.resolve_glossary.input",
    output: "stage.resolve_glossary.output",
    checks: "stage.resolve_glossary.checks",
    model: "stage.resolve_glossary.model",
  },
  approve_glossary: {
    does: "stage.approve_glossary.does",
    input: "stage.approve_glossary.input",
    output: "stage.approve_glossary.output",
    checks: "stage.approve_glossary.checks",
    model: "stage.approve_glossary.model",
    pauses: "stage.approve_glossary.pauses",
  },
  preprocess: {
    does: "stage.preprocess.does",
    input: "stage.preprocess.input",
    output: "stage.preprocess.output",
    checks: "stage.preprocess.checks",
  },
  translate: {
    does: "stage.translate.does",
    input: "stage.translate.input",
    output: "stage.translate.output",
    checks: "stage.translate.checks",
    model: "stage.translate.model",
  },
  rescue_translation: {
    does: "stage.rescue_translation.does",
    input: "stage.rescue_translation.input",
    output: "stage.rescue_translation.output",
    checks: "stage.rescue_translation.checks",
    model: "stage.rescue_translation.model",
  },
  audit_translation: {
    does: "stage.audit_translation.does",
    input: "stage.audit_translation.input",
    output: "stage.audit_translation.output",
    checks: "stage.audit_translation.checks",
    model: "stage.audit_translation.model",
  },
  build_story_context: {
    does: "stage.build_story_context.does",
    input: "stage.build_story_context.input",
    output: "stage.build_story_context.output",
    checks: "stage.build_story_context.checks",
    model: "stage.build_story_context.model",
  },
  audit_consistency: {
    does: "stage.audit_consistency.does",
    input: "stage.audit_consistency.input",
    output: "stage.audit_consistency.output",
    checks: "stage.audit_consistency.checks",
  },
  repair_translation: {
    does: "stage.repair_translation.does",
    input: "stage.repair_translation.input",
    output: "stage.repair_translation.output",
    checks: "stage.repair_translation.checks",
    model: "stage.repair_translation.model",
  },
  reprose_translation: {
    does: "stage.reprose_translation.does",
    input: "stage.reprose_translation.input",
    output: "stage.reprose_translation.output",
    checks: "stage.reprose_translation.checks",
    model: "stage.reprose_translation.model",
  },
  review_repaired: {
    does: "stage.review_repaired.does",
    input: "stage.review_repaired.input",
    output: "stage.review_repaired.output",
    checks: "stage.review_repaired.checks",
    model: "stage.review_repaired.model",
  },
  repair_review: {
    does: "stage.repair_review.does",
    input: "stage.repair_review.input",
    output: "stage.repair_review.output",
    checks: "stage.repair_review.checks",
    model: "stage.repair_review.model",
  },
  validate_repaired: {
    does: "stage.validate_repaired.does",
    input: "stage.validate_repaired.input",
    output: "stage.validate_repaired.output",
    checks: "stage.validate_repaired.checks",
    model: "stage.validate_repaired.model",
  },
  translate_title: {
    does: "stage.translate_title.does",
    input: "stage.translate_title.input",
    output: "stage.translate_title.output",
    checks: "stage.translate_title.checks",
    model: "stage.translate_title.model",
  },
  compile: {
    does: "stage.compile.does",
    input: "stage.compile.input",
    output: "stage.compile.output",
    checks: "stage.compile.checks",
    pauses: "stage.compile.pauses",
  },
  validate_epub: {
    does: "stage.validate_epub.does",
    input: "stage.validate_epub.input",
    output: "stage.validate_epub.output",
    checks: "stage.validate_epub.checks",
  },
};

/**
 * A stage's description in the interface language, for a book or for a
 * subtitle job; undefined for a stage it does not know.
 */
export function stageInfo(stage: string, jobType?: string): StageInfo | undefined {
  const keys = STAGE_INFO[stage];
  if (!keys) return undefined;
  return Object.fromEntries(Object.entries(keys).map(([part, key]) => [part, t(jobKey(key, jobType))])) as StageInfo;
}
