import { t, type MessageKey } from "../i18n";

export type Stage = { name: string; status: string; attempts: number; message: string; updated_at?: string };
export type WorkflowStatus = {
  job_id: string;
  overall: string;
  stages: Stage[];
  configuration: { source_path: string };
};

export const STAGE_LABELS: Record<string, MessageKey> = {
  decompile: "progress.stageName.decompile",
  extract_glossary: "progress.stageName.extractGlossary",
  resolve_glossary: "progress.stageName.resolveGlossary",
  approve_glossary: "progress.stageName.approveGlossary",
  build_story_context: "progress.stageName.buildStoryContext",
  preprocess: "progress.stageName.preprocess",
  translate: "progress.stageName.translate",
  rescue_translation: "progress.stageName.rescueTranslation",
  audit_translation: "progress.stageName.auditTranslation",
  audit_consistency: "progress.stageName.auditConsistency",
  repair_translation: "progress.stageName.repairTranslation",
  reprose_translation: "progress.stageName.reproseTranslation",
  review_repaired: "progress.stageName.reviewRepaired",
  repair_review: "progress.stageName.repairReview",
  validate_repaired: "progress.stageName.validateRepaired",
  translate_title: "progress.stageName.translateTitle",
  compile: "progress.stageName.compile",
  validate_epub: "progress.stageName.validateEpub",
};

export const stageLabel = (name: string) => (STAGE_LABELS[name] ? t(STAGE_LABELS[name]) : name);

export type TabKey = "config" | "glossary" | "progress" | "text" | "review";
export type TabState = "running" | "waiting";

// Which tab covers each pipeline stage.
const STAGE_TAB: Record<string, TabKey> = {
  extract_glossary: "glossary",
  resolve_glossary: "glossary",
  approve_glossary: "glossary",
};

/**
 * The tab holding the job's current stage gets a dot: "running" while that
 * stage works, "waiting" when it needs you (a draft to start, a review gate).
 */
export function tabStates(kind: "draft" | "job" | undefined, overall: string, stages: Stage[]): Partial<Record<TabKey, TabState>> {
  if (kind === "draft") return { config: overall === "starting" ? "running" : "waiting" };
  if (!stages.length || overall === "complete") return {};
  const current = stages.find((s) => ["running", "paused", "failed"].includes(s.status)) ?? stages.find((s) => s.status === "pending");
  if (!current) return {};
  if (current.name === "compile" && current.status === "paused") return { review: "waiting" };
  const tab = STAGE_TAB[current.name] ?? "progress";
  if (current.status === "running") return { [tab]: "running" };
  if (current.name === "approve_glossary" && current.status === "paused") return { glossary: "waiting" };
  // Paused on request, stopped, or failed mid-pipeline: Progress is where to resume.
  return current.status === "pending" ? {} : { progress: "waiting" };
}

/** Which job tabs are waiting on the user. */
export function attentionFrom(status?: WorkflowStatus) {
  const stage = (name: string) => status?.stages.find((s) => s.name === name);
  return {
    glossary: stage("approve_glossary")?.status === "paused",
    review: stage("compile")?.status === "paused",
  };
}

// Paused here means waiting for a person, handled on the Glossary and Review tabs.
const HUMAN_GATES = new Set(["approve_glossary", "compile"]);

export type StageAction = "resume" | "rerun";

/**
 * Pipeline row actions: resume continues where the run stopped and keeps finished
 * work; rerun resets the stage and every later stage, discarding their work.
 */
export function stageActions(stage: Stage): StageAction[] {
  if (stage.status === "completed") return ["rerun"];
  if (stage.status === "failed") return ["resume", "rerun"];
  if (stage.status === "paused" && !HUMAN_GATES.has(stage.name)) return ["resume", "rerun"];
  return [];
}

/**
 * What last happened to a stage, for the Progress table's "Last change" column.
 * A stage's `updated_at` moves only when its status changes, so it dates this
 * action. Null for a stage that has never run.
 */
export function lastStageAction(stage: Stage): string | null {
  if (!stage.updated_at) return null;
  switch (stage.status) {
    case "running": return t("progress.lastChange.started");
    case "completed": return t("progress.lastChange.done");
    case "failed": return t("progress.lastChange.failed");
    case "paused":
      if (HUMAN_GATES.has(stage.name) && !/stopped|paused on request/.test(stage.message)) return t("progress.lastChange.waitingForReview");
      if (stage.message.includes("stopped from the dashboard")) return t("progress.lastChange.stopped");
      return t("progress.lastChange.paused");
    // A pending stage that has run before was reset by a rerun of it or an earlier stage.
    case "pending": return stage.attempts > 0 ? t("progress.lastChange.reset") : null;
    default: return stage.status;
  }
}
