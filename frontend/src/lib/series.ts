/** A series id suggestion from its display name: "The Qel Cycle" -> "the-qel-cycle". */
export function seriesIdFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9._-]+/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "")
    .slice(0, 64);
}

export type Term = {
  term_id: string;
  english: string;
  chinese: string;
  category: string;
  origin: "consensus" | "conflict" | "single_book" | "carried" | "manual";
  books: Record<string, string[]>;
  decision: "pending" | "keep" | "drop";
  decided_by: "rule" | "user" | "llm-accepted";
  reason: string;
  locked_from: string | null;
  suggestion?: { kind: SuggestionKind; chinese: string | null; rationale: string; model: string } | null;
  /** Suggestion kinds a person rejected; those tasks skip the term. */
  dismissed?: SuggestionKind[];
  /** Occurrences in each member book's source text, glossary or not. */
  mentions?: Record<string, number>;
};

type SuggestionKind = "resolve" | "drop_generic" | "promote";
const TASK_KIND: Record<"conflicts" | "generic" | "promote", SuggestionKind> = {
  conflicts: "resolve",
  generic: "drop_generic",
  promote: "promote",
};

export type SuggestTask = "conflicts" | "generic" | "promote";

/** Mirrors ``series_llm.eligible``: which terms a suggestion run would send. */
export function suggestionEligible(term: Term, task: SuggestTask): boolean {
  if (term.suggestion || term.decided_by !== "rule" || term.dismissed?.includes(TASK_KIND[task])) return false;
  if (task === "conflicts") return term.origin === "conflict" && term.decision === "pending";
  if (task === "generic") return term.locked_from === null && (term.origin === "consensus" || term.origin === "conflict");
  return term.origin === "single_book" && term.decision === "drop";
}

type FixedView = "suggested" | "pending" | "conflict" | "keep" | "single_book" | "elsewhere" | "carried" | "drop" | "all";
/** A fixed view, or ``book:<job id>`` for one book's single-book terms. */
export type WorkbenchView = FixedView | `book:${string}`;

/** Sidebar views of the series workbench, in display order. */
export const WORKBENCH_VIEWS: [FixedView, string][] = [
  ["suggested", "LLM suggestions"],
  ["pending", "Needs a decision"],
  ["conflict", "Conflicts"],
  ["keep", "To publish"],
  ["single_book", "Single-book terms"],
  ["elsewhere", "Found in other books"],
  ["carried", "Already published"],
  ["drop", "Dropped"],
  ["all", "All terms"],
];

/** The one book whose glossary has a single-book term. */
export const singleBookOf = (term: Term) => (term.origin === "single_book" ? Object.keys(term.books)[0] ?? "" : "");

/** Member books whose source text mentions the term. */
export const mentioningBooks = (term: Term) => Object.keys(term.mentions ?? {}).filter((job) => (term.mentions ?? {})[job] > 0);

export function matchesView(term: Term, view: WorkbenchView): boolean {
  if (view.startsWith("book:")) return singleBookOf(term) === view.slice(5);
  switch (view) {
    case "elsewhere": return term.origin === "single_book" && mentioningBooks(term).length >= 2;
    case "suggested": return !!term.suggestion;
    case "pending": return term.decision === "pending";
    case "conflict": return term.origin === "conflict";
    case "keep": return term.decision === "keep";
    case "single_book": return term.origin === "single_book";
    case "carried": return term.locked_from !== null;
    case "drop": return term.decision === "drop";
    default: return true;
  }
}
