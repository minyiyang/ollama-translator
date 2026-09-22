import { describe, expect, it } from "vitest";
import { matchesTextQuery, matchesTextView, retainTextDocumentId, textRowClass, type TextFilterSegment } from "./text";

const base: TextFilterSegment = {
  source: "Chapter One",
  text: "第一章",
  state: "pipeline",
  in_review_queue: false,
  flagged: false,
};

describe("Text page filters", () => {
  it("retains the selected chapter across outline polling", () => {
    const chapters = [{ document_id: "chapter-1" }, { document_id: "chapter-2" }];
    expect(retainTextDocumentId("chapter-2", chapters)).toBe("chapter-2");
    expect(retainTextDocumentId("", chapters)).toBe("chapter-1");
    expect(retainTextDocumentId("removed", chapters)).toBe("chapter-1");
    expect(retainTextDocumentId("removed", [])).toBe("");
  });

  it("uses the dynamic flagged state rather than historical findings", () => {
    const resolved = { ...base, state: "edited" as const, flagged: false };
    expect(matchesTextView(resolved, "flagged")).toBe(false);
    expect(matchesTextView({ ...base, flagged: true }, "flagged")).toBe(true);
  });

  it("matches queue, edited, and conflict views independently", () => {
    expect(matchesTextView({ ...base, in_review_queue: true }, "queue")).toBe(true);
    expect(matchesTextView({ ...base, state: "edited" }, "edited")).toBe(true);
    expect(matchesTextView({ ...base, state: "conflict" }, "conflict")).toBe(true);
    expect(matchesTextView(base, "all")).toBe(true);
  });

  it("searches source and effective translation case-insensitively", () => {
    expect(matchesTextQuery(base, "chapter")).toBe(true);
    expect(matchesTextQuery(base, "第一")).toBe(true);
    expect(matchesTextQuery(base, "missing")).toBe(false);
  });

  it("prioritizes conflict and queue styling over flagged styling", () => {
    expect(textRowClass({ ...base, state: "conflict", flagged: true })).toBe("conflict");
    expect(textRowClass({ ...base, in_review_queue: true, flagged: true })).toBe("queued");
    expect(textRowClass({ ...base, flagged: true })).toBe("flagged");
    expect(textRowClass(base)).toBe("");
  });
});
