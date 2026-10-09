// Labels for the fixed words the server sends (statuses, tiers, categories,
// severities, …). Each known value has a catalog message whose English text is
// the value as it was always shown; a value the table lacks is shown as sent.

import { t, type MessageKey } from "../i18n";

type Labels = Record<string, MessageKey>;

const has = (labels: Labels, value: string) => Object.prototype.hasOwnProperty.call(labels, value);
const label = (labels: Labels, value: string): string => (has(labels, value) ? t(labels[value]) : value);

// A stage's status (book_agent/state.py), a job's overall status and a run's
// result (book_agent/workflow.py), a draft's (book_agent/web), and the
// dashboard's own "pausing".
const STATUS: Labels = {
  pending: "status.pending",
  running: "status.running",
  completed: "status.completed",
  failed: "status.failed",
  paused: "status.paused",
  complete: "status.complete",
  cancelled: "status.cancelled",
  draft: "status.draft",
  starting: "status.starting",
  pausing: "status.pausing",
};
export const statusLabel = (value: string) => label(STATUS, value);

// A language profile's tier (book_agent/languages.py).
const TIER: Labels = {
  tuned: "tier.tuned",
  profiled: "tier.profiled",
  generic: "tier.generic",
};
export const tierLabel = (value: string) => label(TIER, value);

// GlossaryCategory (book_agent/schemas.py): the server sends the member's value.
const GLOSSARY_CATEGORY: Labels = {
  "人名": "category.person",
  "地名": "category.place",
  "组织": "category.organization",
  "物品": "category.item",
  "技术": "category.technology",
  "概念": "category.concept",
  "术语": "category.term",
  "其他": "category.other",
};
/** A glossary category's name: the catalog's, else the server's own label, else the value. */
export const glossaryCategoryLabel = (value: string, serverLabels?: Record<string, string> | null): string =>
  has(GLOSSARY_CATEGORY, value) ? t(GLOSSARY_CATEGORY[value]) : serverLabels?.[value] ?? value;

// AuditSeverity (book_agent/audit.py), "info" on a passed verification
// (book_agent/stages/compile.py), and "error" for a check that could not run.
const SEVERITY: Labels = {
  low: "severity.low",
  medium: "severity.medium",
  high: "severity.high",
  info: "severity.info",
  error: "severity.error",
};
export const severityLabel = (value: string) => label(SEVERITY, value);

// AuditCategory (book_agent/audit.py), the categories the review report adds
// (book_agent/stages/compile.py), and "check" for a check that could not run.
const FINDING_CATEGORY: Labels = {
  structure: "enum.findingCategory.structure",
  empty: "enum.findingCategory.empty",
  untranslated: "enum.findingCategory.untranslated",
  omission: "enum.findingCategory.omission",
  addition: "enum.findingCategory.addition",
  duplication: "enum.findingCategory.duplication",
  glossary: "enum.findingCategory.glossary",
  mistranslation: "enum.findingCategory.mistranslation",
  naturalness: "enum.findingCategory.naturalness",
  ai_style: "enum.findingCategory.aiStyle",
  punctuation: "enum.findingCategory.punctuation",
  consistency: "enum.findingCategory.consistency",
  readability: "enum.findingCategory.readability",
  repair: "enum.findingCategory.repair",
  verification: "enum.findingCategory.verification",
  quantity: "enum.findingCategory.quantity",
  check: "enum.findingCategory.check",
};
export const findingCategoryLabel = (value: string) => label(FINDING_CATEGORY, value);

// Which check raised a final-review finding (book_agent/stages/compile.py).
const FINDING_ORIGIN: Labels = {
  initial_translation_audit: "enum.findingOrigin.initialTranslationAudit",
  initial_quantity_audit: "enum.findingOrigin.initialQuantityAudit",
  repair_history: "enum.findingOrigin.repairHistory",
  repair: "enum.findingOrigin.repair",
  final_deterministic_audit: "enum.findingOrigin.finalDeterministicAudit",
  final_semantic_verification: "enum.findingOrigin.finalSemanticVerification",
  final_quantity_audit: "enum.findingOrigin.finalQuantityAudit",
  book_consistency: "enum.findingOrigin.bookConsistency",
};
export const findingOriginLabel = (value: string) => label(FINDING_ORIGIN, value);

// Why a passage is in final review (book_agent/stages/compile.py).
const REVIEW_KIND: Labels = {
  approval_required: "enum.reviewKind.approvalRequired",
  defect: "enum.reviewKind.defect",
};
export const reviewKindLabel = (value: string) => label(REVIEW_KIND, value);

// The drafts of a passage final review can load (book_agent/stages/compile.py).
const VERSION: Labels = {
  raw_translation: "enum.version.rawTranslation",
  semantic_repair: "enum.version.semanticRepair",
  prose_rewrite_candidate: "enum.version.proseRewriteCandidate",
  current_validated_draft: "enum.version.currentValidatedDraft",
};
/** A draft's name. Drafts with the same text arrive as one, their names joined by ", ". */
export const versionLabel = (value: string) => value.split(", ").map((name) => label(VERSION, name)).join(", ");

// EditAction (book_agent/text_edits.py).
const EDIT_ACTION: Labels = {
  edit: "enum.editAction.edit",
  revert: "enum.editAction.revert",
  keep: "enum.editAction.keep",
  take_pipeline: "enum.editAction.takePipeline",
};
export const editActionLabel = (value: string) => label(EDIT_ACTION, value);

// GlossaryApprovalRecord.result and .mode (book_agent/schemas.py).
const APPROVAL_RESULT: Labels = {
  approved: "enum.approvalResult.approved",
  rejected: "enum.approvalResult.rejected",
  revised: "enum.approvalResult.revised",
  pending: "enum.approvalResult.pending",
  failed: "enum.approvalResult.failed",
};
export const approvalResultLabel = (value: string) => label(APPROVAL_RESULT, value);

const APPROVAL_MODE: Labels = {
  deterministic: "enum.approvalMode.deterministic",
  llm: "enum.approvalMode.llm",
};
export const approvalModeLabel = (value: string) => label(APPROVAL_MODE, value);

// How an approved glossary was reviewed (book_agent/stages/glossary.py).
const REVIEW_MODE: Labels = {
  llm: "enum.reviewMode.llm",
  human: "enum.reviewMode.human",
  none: "enum.reviewMode.none",
};
export const reviewModeLabel = (value: string) => label(REVIEW_MODE, value);

// The glossary review a configuration asks for (book_agent/web/setup.py).
const GLOSSARY_REVIEW: Labels = {
  LLM: "enum.glossaryReview.llm",
  human: "enum.glossaryReview.human",
  none: "enum.glossaryReview.none",
};
export const glossaryReviewLabel = (value: string) => label(GLOSSARY_REVIEW, value);

// TranslationStyle (book_agent/styles.py).
const STYLE: Labels = {
  faithful: "enum.style.faithful",
  natural: "enum.style.natural",
  literary: "enum.style.literary",
  concise: "enum.style.concise",
  classic: "enum.style.classic",
  young_adult: "enum.style.youngAdult",
  fantasy: "enum.style.fantasy",
  science_fiction: "enum.style.scienceFiction",
  custom: "enum.style.custom",
};
export const styleLabel = (value: string) => label(STYLE, value);

// What a configured model is used for (model_roles in book_agent/web/setup.py).
const ROLE: Labels = {
  translation: "enum.role.translation",
  "glossary resolution/approval": "enum.role.glossaryResolutionApproval",
  "translation, glossary resolution/approval": "enum.role.translationAndGlossary",
  "glossary extraction": "enum.role.glossaryExtraction",
  "semantic audit": "enum.role.semanticAudit",
  "repair verification": "enum.role.repairVerification",
  "targeted repair": "enum.role.targetedRepair",
  "quantity audit": "enum.role.quantityAudit",
  "quantity escalation": "enum.role.quantityEscalation",
  "prose rewrite": "enum.role.proseRewrite",
  "rewrite verification": "enum.role.rewriteVerification",
};
const FALLBACK_ROLE = /^fallback translation #(\d+)$/;
export function roleLabel(value: string): string {
  const fallback = FALLBACK_ROLE.exec(value);
  return fallback ? t("enum.role.fallbackTranslation", { number: fallback[1] }) : label(ROLE, value);
}

// How far a book's glossary has come, for a book a series could take (book_agent/series.py).
const GLOSSARY_BOUNDARY: Labels = {
  approved: "enum.glossaryBoundary.approved",
  resolved: "enum.glossaryBoundary.resolved",
  "not ready": "enum.glossaryBoundary.notReady",
};
export const glossaryBoundaryLabel = (value: string) => label(GLOSSARY_BOUNDARY, value);
