export type TextView = "all" | "flagged" | "queue" | "edited" | "conflict";

export type TextFilterSegment = {
  source: string;
  text: string;
  state: "pipeline" | "edited" | "conflict" | "orphaned";
  in_review_queue: boolean;
  flagged: boolean;
};

export function retainTextDocumentId(
  current: string,
  chapters: ReadonlyArray<{ document_id: string }>,
): string {
  if (current && chapters.some((chapter) => chapter.document_id === current)) return current;
  return chapters[0]?.document_id ?? "";
}

export function matchesTextView(segment: TextFilterSegment, view: TextView): boolean {
  if (view === "flagged") return segment.flagged;
  if (view === "queue") return segment.in_review_queue;
  if (view === "edited") return segment.state === "edited";
  if (view === "conflict") return segment.state === "conflict";
  return true;
}

export function matchesTextQuery(segment: TextFilterSegment, query: string): boolean {
  const needle = query.trim().toLowerCase();
  return !needle
    || segment.source.toLowerCase().includes(needle)
    || segment.text.toLowerCase().includes(needle);
}

export function textRowClass(segment: TextFilterSegment): string {
  if (segment.state === "conflict") return "conflict";
  if (segment.in_review_queue) return "queued";
  if (segment.flagged) return "flagged";
  return "";
}
