import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ComponentProps } from "react";
import { describe, expect, it, vi } from "vitest";
import { NO_OPT_INS, type ImportItem, type ImportPreview, type ImportResult, type SkippedItem } from "../lib/xliffImport";
import { ImportPreviewCard, LastImportCard } from "./XliffImport";

let unit = 0;
const item = (overrides: Partial<ImportItem> = {}): ImportItem => {
  unit += 1;
  return {
    unit_id: `u${unit}`,
    segment_id: `D0000-S${String(unit).padStart(6, "0")}`,
    document_id: "chapter-1",
    category: "import",
    message: "",
    imported_text: "新译文。",
    stale: false,
    edited_since_export: false,
    hard: [],
    overridable: [],
    ...overrides,
  };
};
const finding = { category: "naturalness", severity: "medium", message: "reads stiffly" };

// Two clean imports, one of each opt-in, one unchanged, and three skipped for good.
const ITEMS: ImportItem[] = [
  item(),
  item(),
  item({ category: "stale", stale: true }),
  item({ category: "edited_since_export", edited_since_export: true }),
  item({ category: "needs_override", overridable: [finding] }),
  item({ category: "unchanged" }),
  item({ category: "fails_checks", hard: [finding] }),
  item({ category: "unknown_id", unit_id: "ghost-1", segment_id: null, document_id: null }),
  item({ category: "unknown_id", unit_id: "ghost-2", segment_id: null, document_id: null }),
];

const preview = (overrides: Partial<ImportPreview> = {}): ImportPreview => ({
  import_id: "imp 1", file_name: "alice.xlf", created_at: "2026-09-21T10:00:00+08:00", warnings: [], counts: {}, items: ITEMS, ...overrides,
});

type PreviewProps = ComponentProps<typeof ImportPreviewCard>;

function renderPreview(overrides: Partial<PreviewProps> = {}) {
  const props: PreviewProps = {
    jobId: "my book",
    preview: preview(),
    options: NO_OPT_INS,
    onOptions: vi.fn(),
    filter: "will_import",
    onFilter: vi.fn(),
    notInFile: 4,
    reason: "Translator pass",
    onReason: vi.fn(),
    applying: false,
    blocked: "",
    onApply: vi.fn(),
    onCancel: vi.fn(),
    ...overrides,
  };
  render(<ImportPreviewCard {...props} />);
  return { props, user: userEvent.setup(), card: screen.getByRole("region", { name: "Import preview" }) };
}

describe("Reviewing a translator's XLIFF file before importing it", () => {
  it("names the file and offers its segment-by-segment report", () => {
    renderPreview();
    expect(screen.getByRole("heading", { name: "Import preview · alice.xlf" })).toBeInTheDocument();
    const report = screen.getByRole("link", { name: "Download report" });
    expect(report).toHaveAttribute("href", "/api/jobs/my%20book/text/import/report?import_id=imp%201");
    expect(report).toHaveAttribute("download");
  });

  it("counts what will import, is skipped, is unchanged, and is not in the file", () => {
    renderPreview();
    const filters = within(screen.getByRole("radiogroup", { name: "Import filter" })).getAllByRole("radio");
    expect(filters.map((f) => f.textContent)).toEqual(["Will import 2", "Skipped 6", "Unchanged 1", "Not in file 4", "All"]);
    expect(screen.getByRole("radio", { name: "Will import 2" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Skipped 6" })).not.toBeChecked();
  });

  it("narrows the table to the group the user clicks", async () => {
    const { props, user } = renderPreview();
    await user.click(screen.getByRole("radio", { name: "Skipped 6" }));
    expect(props.onFilter).toHaveBeenCalledExactlyOnceWith("skipped");
    await user.click(screen.getByRole("radio", { name: "All" }));
    expect(props.onFilter).toHaveBeenLastCalledWith("all");
  });

  it("says why segments are skipped, the commonest reason first", () => {
    renderPreview();
    expect(screen.getByText("Skipped: unknown ID 2 · stale 1 · edited since export 1 · needs override 1 · fails checks 1")).toBeInTheDocument();
  });

  it("names the segments of the file that are not in this book", () => {
    renderPreview();
    expect(screen.getByText("2 units in the file are not in this book: ghost-1, ghost-2")).toBeInTheDocument();
  });

  it("says “1 unit is” when only one is not in this book", () => {
    renderPreview({ preview: preview({ items: [item(), item({ category: "unknown_id", unit_id: "ghost", segment_id: null })] }) });
    expect(screen.getByText("1 unit in the file is not in this book: ghost")).toBeInTheDocument();
  });

  it("names at most eight of them and points to the report for the rest", () => {
    const ghosts = Array.from({ length: 10 }, (_, i) => item({ category: "unknown_id", unit_id: `g${i}`, segment_id: null }));
    renderPreview({ preview: preview({ items: ghosts }) });
    expect(screen.getByText("10 units in the file are not in this book: g0, g1, g2, g3, g4, g5, g6, g7, … (all are in the report)")).toBeInTheDocument();
  });

  it("offers to include the doubtful segments too, saying how many each choice adds", () => {
    renderPreview();
    expect(screen.getByRole("checkbox", { name: /Also import 1 stale segment \(the pipeline text changed after export\)/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Also import 1 segment edited in the Text tab after export/ })).not.toBeChecked();
    expect(screen.getByRole("checkbox", { name: /Also import 1 segment with overridable findings/ })).not.toBeChecked();
  });

  it("offers no such choice when there is nothing doubtful", () => {
    renderPreview({ preview: preview({ items: [item(), item({ category: "unchanged" })] }) });
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
    expect(screen.queryByText(/^Skipped:/)).not.toBeInTheDocument();
    expect(screen.queryByText(/not in this book/)).not.toBeInTheDocument();
  });

  it("ticking one of those choices leaves the others as they were", async () => {
    const { props, user } = renderPreview({ options: { ...NO_OPT_INS, include_edited: true } });
    await user.click(screen.getByRole("checkbox", { name: /stale segment/ }));
    expect(props.onOptions).toHaveBeenCalledExactlyOnceWith({ include_stale: true, include_edited: true, include_overridable: false });

    await user.click(screen.getByRole("checkbox", { name: /edited in the Text tab/ }));
    expect(props.onOptions).toHaveBeenLastCalledWith({ include_stale: false, include_edited: false, include_overridable: false });
  });

  it("counts the segments the user chose to include among those that will be imported", () => {
    renderPreview({ options: { include_stale: true, include_edited: true, include_overridable: true } });
    expect(screen.getByRole("radio", { name: "Will import 5" })).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: "Skipped 3" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Import 5" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: /stale segment/ })).toBeChecked();
  });

  it("shows the file's warnings", () => {
    renderPreview({ preview: preview({ warnings: ["The file was exported from another job.", "3 units have no target."] }) });
    expect(screen.getByText("The file was exported from another job.")).toBeInTheDocument();
    expect(screen.getByText("3 units have no target.")).toBeInTheDocument();
  });

  describe("applying", () => {
    it("imports with a reason", async () => {
      const { props, user } = renderPreview();
      expect(screen.getByRole("textbox", { name: "Import reason" })).toHaveValue("Translator pass");
      expect(screen.getByText("Nothing is written until you import.")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Import 2" }));
      expect(props.onApply).toHaveBeenCalledOnce();
    });

    it("takes the reason as it is typed", async () => {
      const { props, user } = renderPreview({ reason: "" });
      await user.type(screen.getByRole("textbox", { name: "Import reason" }), "S");
      expect(props.onReason).toHaveBeenCalledExactlyOnceWith("S");
    });

    it.each([["", "no reason"], ["  ok  ", "a reason shorter than three characters"]])("cannot import with %j (%s)", (reason) => {
      renderPreview({ reason });
      expect(screen.getByRole("button", { name: "Import 2" })).toBeDisabled();
    });

    it("cannot import when nothing would be imported", () => {
      renderPreview({ preview: preview({ items: [item({ category: "unchanged" })] }) });
      expect(screen.getByRole("button", { name: "Import 0" })).toBeDisabled();
    });

    it("cannot import while blocked, and says why", () => {
      renderPreview({ blocked: "The job is running; pause or stop it first." });
      const button = screen.getByRole("button", { name: "Import 2" });
      expect(button).toBeDisabled();
      expect(button.parentElement).toHaveAttribute("title", "The job is running; pause or stop it first.");
    });

    it("locks both buttons while the import is being applied", () => {
      renderPreview({ applying: true });
      expect(screen.getByRole("button", { name: "Importing…" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Cancel import" })).toBeDisabled();
    });

    it("cancels the import", async () => {
      const { props, user } = renderPreview();
      await user.click(screen.getByRole("button", { name: "Cancel import" }));
      expect(props.onCancel).toHaveBeenCalledOnce();
      expect(props.onApply).not.toHaveBeenCalled();
    });
  });

  it("offers only the report and Cancel when the import can no longer be checked", async () => {
    const { props, user, card } = renderPreview({ preview: preview({ error: "the draft changed; export again" }) });
    expect(card).toHaveTextContent("This import can't be checked right now: the draft changed; export again");
    expect(screen.queryByRole("radiogroup")).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: "Import reason" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Import/ })).not.toBeInTheDocument();
    expect(screen.queryByText("Nothing is written until you import.")).not.toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download report" })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel import" }));
    expect(props.onCancel).toHaveBeenCalledOnce();
  });
});

const skipped = (overrides: Partial<SkippedItem>): SkippedItem => ({
  unit_id: "u1", segment_id: "D0000-S000001", document_id: "chapter-1", category: "fails_checks", message: "", imported_text: "新译文。", ...overrides,
});

const result = (overrides: Partial<ImportResult> = {}): ImportResult => ({
  import_id: "imp 1",
  file_name: "alice.xlf",
  applied_at: "2026-09-21T10:05:00+08:00",
  applied: [{ segment_id: "D0000-S000009", document_id: "chapter-1", event_id: "E000001" }],
  skipped: [
    skipped({ unit_id: "u1", segment_id: "D0000-S000001", message: "protected marker removed" }),
    skipped({ unit_id: "ghost-1", segment_id: null, document_id: null, category: "unknown_id", imported_text: null }),
  ],
  unchanged_count: 3,
  dropped_at_apply: 0,
  ...overrides,
});

function renderLast(overrides: Partial<ImportResult> = {}) {
  const onOpen = vi.fn();
  const onDismiss = vi.fn();
  const value = result(overrides);
  render(<LastImportCard jobId="my book" result={value} onOpen={onOpen} onDismiss={onDismiss} />);
  return { onOpen, onDismiss, value, user: userEvent.setup(), card: screen.getByRole("region", { name: "Last import" }) };
}

describe("The summary of the last import", () => {
  it("summarizes the import and links its report", () => {
    const { card } = renderLast();
    expect(card).toHaveTextContent("Last import · alice.xlf: imported 1, skipped 2");
    expect(card).not.toHaveTextContent("failed the checks when applied");
    expect(screen.getByRole("link", { name: "Download report" })).toHaveAttribute("href", "/api/jobs/my%20book/text/import/report?import_id=imp%201");
  });

  it("says how many segments failed the checks at the last moment", () => {
    const { card } = renderLast({ dropped_at_apply: 2 });
    expect(card).toHaveTextContent("imported 1, skipped 2 (2 failed the checks when applied)");
  });

  it("keeps the skipped segments folded until asked, then lists why each was skipped", async () => {
    const { user } = renderLast();
    const toggle = screen.getByRole("button", { name: "Show skipped ▾" });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("table")).not.toBeInTheDocument();

    await user.click(toggle);
    expect(screen.getByRole("button", { name: "Hide skipped ▴" })).toHaveAttribute("aria-expanded", "true");
    const rows = within(screen.getByRole("table")).getAllByRole("row");
    expect(rows[0]).toHaveTextContent("D0000-S000001fails checks: protected marker removed");
    expect(rows[1]).toHaveTextContent("ghost-1unknown ID"); // no segment: the unit id, and no message

    await user.click(screen.getByRole("button", { name: "Hide skipped ▴" }));
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
  });

  it("opens a skipped segment in the editor, but only one that exists in the book", async () => {
    const { user, onOpen, value } = renderLast();
    await user.click(screen.getByRole("button", { name: "Show skipped ▾" }));
    const open = screen.getAllByRole("button", { name: "Open" });
    expect(open).toHaveLength(1);

    await user.click(open[0]);
    expect(onOpen).toHaveBeenCalledExactlyOnceWith(value.skipped[0]);
  });

  it("has nothing to unfold when nothing was skipped", () => {
    const { card } = renderLast({ skipped: [] });
    expect(card).toHaveTextContent("imported 1, skipped 0");
    expect(screen.queryByRole("button", { name: /skipped/ })).not.toBeInTheDocument();
  });

  it("is dismissed on request", async () => {
    const { user, onDismiss } = renderLast();
    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalledOnce();
  });
});
