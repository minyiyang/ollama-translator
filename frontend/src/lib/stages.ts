export type Stage = { name: string; status: string; attempts: number; message: string; updated_at?: string };
export type WorkflowStatus = {
  job_id: string;
  overall: string;
  stages: Stage[];
  configuration: { source_path: string };
};

export const STAGE_LABELS: Record<string, string> = {
  decompile: "Decompile source",
  extract_glossary: "Extract glossary",
  resolve_glossary: "Resolve glossary",
  approve_glossary: "Approve glossary",
  preprocess: "Preprocess",
  translate: "Translate",
  rescue_translation: "Rescue with fallback models",
  audit_translation: "Audit translation",
  repair_translation: "Repair findings",
  reprose_translation: "Prose rewrite",
  review_repaired: "Review repairs",
  repair_review: "Repair review feedback",
  validate_repaired: "Validate draft",
  compile: "Compile EPUB",
  validate_epub: "Validate EPUB",
};

export const stageLabel = (name: string) => STAGE_LABELS[name] ?? name;

export type TabKey = "config" | "glossary" | "progress" | "review";
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
