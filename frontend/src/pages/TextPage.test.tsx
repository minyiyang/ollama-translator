import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { jsonResponse, mockApi } from "../test/mockApi";

// -- fixtures -----------------------------------------------------------------

const jobInfo = (overrides: Record<string, unknown> = {}) => ({
  job_id: "demo",
  kind: "job",
  overall: "complete",
  source: "book.epub",
  config: "demo.yaml",
  stages: [],
  running: false,
  pause_requested: false,
  can_stop: false,
  process: null,
  ...overrides,
});

const counts = { flagged_count: 0, in_review_queue_count: 0, edited_count: 0, conflict_count: 0 };

const outline = (overrides: Record<string, unknown> = {}) => ({
  available: true,
  editable: true,
  chapters: [
    { document_id: "chapter-1", order: 1, title: "Chapter One", segment_count: 3, ...counts, in_review_queue_count: 1, edited_count: 1, conflict_count: 1 },
    { document_id: "chapter-2", order: 2, title: "Chapter Two", segment_count: 1, ...counts },
  ],
  totals: { documents: 2, segments: 4, flagged: 0, in_review_queue: 1, edited: 1, conflicts: 1 },
  uncompiled_edit_count: 0,
  ...overrides,
});

const segment = (overrides: Record<string, unknown>) => ({
  state: "pipeline",
  edit_revision: "",
  findings: [],
  flagged: false,
  in_review_queue: false,
  last_edit: null,
  ...overrides,
});

const chapterOne = (editable = true) => ({
  document_id: "chapter-1",
  title: "Chapter One",
  order: 1,
  editable,
  segments: [
    segment({
      segment_id: "D0000-S000001",
      source: "Hello world.",
      pipeline_text: "你好，世界。",
      text: "你好，世界。",
      base_target_sha256: "hash-1",
      in_review_queue: true,
    }),
    segment({
      segment_id: "D0000-S000002",
      source: "Nested paragraph.",
      pipeline_text: "嵌套的段落。",
      text: "嵌套段落。",
      state: "edited",
      edit_revision: "E000001",
      base_target_sha256: "hash-2",
      last_edit: { action: "edit", author: "minyi", at: "2026-09-21T10:00:00+08:00", reason: "Tighter.", based_on: "嵌套的段落。" },
    }),
    segment({
      segment_id: "D0000-S000003",
      source: "Plain item.",
      pipeline_text: "新的流水线译文。",
      text: "我的译文。",
      state: "conflict",
      edit_revision: "E000002",
      base_target_sha256: "hash-3",
      last_edit: { action: "edit", author: "minyi", at: "2026-09-21T11:00:00+08:00", reason: "Mine.", based_on: "旧的流水线译文。" },
    }),
  ],
});

const chapterTwo = () => ({
  document_id: "chapter-2",
  title: "Chapter Two",
  order: 2,
  editable: true,
  segments: [
    segment({ segment_id: "D0001-S000001", source: "Second chapter.", pipeline_text: "第二章。", text: "第二章。", base_target_sha256: "hash-4" }),
  ],
});

const CLEAN_CHECK = { segment_id: "x", hard: [], overridable: [] };

function textApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/jobs/demo/info": jobInfo(),
    "GET /api/jobs/demo/text": outline(),
    "GET /api/jobs/demo/text/chapter": (_body: unknown, url: URL) =>
      url.searchParams.get("document_id") === "chapter-2" ? chapterTwo() : chapterOne(),
    "POST /api/jobs/demo/text/check": CLEAN_CHECK,
    "POST /api/jobs/demo/text/edit": { event_id: "E000009", action: "edit" },
    "GET /api/jobs/demo/text/import": { pending: null, last: null },
    ...overrides,
  });
}

function renderTextTab() {
  window.history.pushState({}, "", "/jobs/demo/text");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

const rowOf = (source: string) => screen.getByText(source).closest("tr") as HTMLElement;
const editorOf = (text: string) => screen.getByDisplayValue(text) as HTMLTextAreaElement;

// -- tests --------------------------------------------------------------------

describe("Text tab", () => {
  describe("reading the book", () => {
    it("sits between Progress and Final review in the job tabs", async () => {
      textApi();
      renderTextTab();
      const tabs = within(await screen.findByRole("navigation")).getAllByRole("link");
      expect(tabs.map((tab) => tab.textContent)).toEqual(["Config", "Glossary", "Progress", "Text", "Final review"]);
    });

    it("lists chapters with their counts and shows the first chapter as paired rows", async () => {
      textApi();
      renderTextTab();

      expect(await screen.findByText("Hello world.")).toBeInTheDocument();
      const sidebar = screen.getByRole("complementary");
      expect(within(sidebar).getByRole("button", { name: /Chapter One/ })).toHaveTextContent("1 conflict · 1 in review · 1 edited");
      expect(within(sidebar).getByRole("button", { name: /Chapter Two/ })).toBeInTheDocument();
      expect(screen.getByText("3 of 3 segments")).toBeInTheDocument();

      expect(within(rowOf("Hello world.")).getByText("你好，世界。")).toBeInTheDocument();
      expect(within(rowOf("Hello world.")).getByRole("link", { name: "Open in Final review →" })).toHaveAttribute(
        "href",
        "/jobs/demo/review",
      );
      expect(within(rowOf("Nested paragraph.")).getByText("edited")).toBeInTheDocument();
      expect(within(rowOf("Plain item.")).getByRole("button", { name: "Resolve conflict" })).toBeInTheDocument();
    });

    it("offers the whole book as an XLIFF download next to the totals", async () => {
      textApi();
      renderTextTab();
      const link = await screen.findByRole("link", { name: "⤓ Export XLIFF" });
      expect(link).toHaveAttribute("href", "/api/jobs/demo/text/export?format=xliff");
      expect(link).toHaveAttribute("download");
      expect(link.closest(".stats")).not.toBeNull();
    });

    it("loads another chapter when it is picked in the sidebar", async () => {
      const api = textApi();
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(screen.getByRole("button", { name: /Chapter Two/ }));

      expect(await screen.findByText("Second chapter.")).toBeInTheDocument();
      expect(screen.queryByText("Hello world.")).not.toBeInTheDocument();
      expect(api.requested("/api/jobs/demo/text/chapter?document_id=chapter-2")).toBe(true);
    });

    it("filters rows by state and by search text", async () => {
      textApi();
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(screen.getByRole("radio", { name: "Edited" }));
      expect(screen.getByText("Nested paragraph.")).toBeInTheDocument();
      expect(screen.queryByText("Hello world.")).not.toBeInTheDocument();
      expect(screen.getByText("1 of 3 segments")).toBeInTheDocument();

      await user.click(screen.getByRole("radio", { name: "All" }));
      await user.type(screen.getByPlaceholderText("Search source or translation"), "plain");
      expect(screen.getByText("Plain item.")).toBeInTheDocument();
      expect(screen.queryByText("Nested paragraph.")).not.toBeInTheDocument();

      await user.clear(screen.getByPlaceholderText("Search source or translation"));
      await user.type(screen.getByPlaceholderText("Search source or translation"), "no such text");
      expect(screen.getByText("No segments match.")).toBeInTheDocument();
    });

    it("is read-only before the validated draft exists", async () => {
      textApi({
        "GET /api/jobs/demo/text": outline({ editable: false }),
        "GET /api/jobs/demo/text/chapter": chapterOne(false),
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      expect(screen.getByText(/Read-only: showing the latest translation stage/)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "⤓ Export XLIFF" })).not.toBeInTheDocument(); // needs the validated draft
      await user.click(screen.getByText("你好，世界。"));
      expect(document.querySelector("textarea.editor")).toBeNull();
    });

    it("explains that nothing is shown until a translation exists", async () => {
      const api = textApi({ "GET /api/jobs/demo/text": outline({ available: false, chapters: [] }) });
      renderTextTab();
      expect(await screen.findByText(/The Text tab fills in once a translation exists/)).toBeInTheDocument();
      expect(api.calls.some((call) => call.path.startsWith("/api/jobs/demo/text/chapter"))).toBe(false);
    });

    it("tells a draft job to start first and makes no text requests", async () => {
      const api = textApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "draft" }) });
      renderTextTab();
      expect(await screen.findByText(/there is no book text to show/)).toBeInTheDocument();
      expect(api.requested("/api/jobs/demo/text")).toBe(false);
    });
  });

  describe("editing a segment", () => {
    it("checks the new text and saves it with a reason, the base hash, and the edit revision", async () => {
      const api = textApi();
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Hello world.")).getByRole("button", { name: "Edit" }));
      const editor = editorOf("你好，世界。");
      const save = screen.getByRole("button", { name: "Save" });
      expect(save).toBeDisabled();

      await user.clear(editor);
      await user.type(editor, "你好，新世界。");
      expect(await screen.findByText("Passes deterministic validation.")).toBeInTheDocument();
      expect(screen.getByText("Changes vs pipeline text")).toBeInTheDocument();
      expect(save).toBeDisabled(); // still no reason

      await user.type(screen.getByPlaceholderText("Why you are making this change"), "Clearer wording.");
      expect(save).toBeEnabled();
      await user.click(save);

      expect(await screen.findByText("Edit saved.")).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/edit")).toEqual([
        {
          segment_id: "D0000-S000001",
          text: "你好，新世界。",
          reason: "Clearer wording.",
          base_target_sha256: "hash-1",
          expected_event_id: "",
        },
      ]);
      expect(document.querySelector("textarea.editor")).toBeNull();
    });

    it("never saves past a hard-blocking finding, even with a reason", async () => {
      const api = textApi({
        "POST /api/jobs/demo/text/check": {
          segment_id: "D0000-S000001",
          hard: [{ category: "structure", severity: "high", message: "protected marker removed" }],
          overridable: [],
        },
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Hello world.")).getByRole("button", { name: "Edit" }));
      await user.type(editorOf("你好，世界。"), "！");
      expect(await screen.findByText("Cannot be saved:")).toBeInTheDocument();
      expect(screen.getByText(/protected marker removed/)).toBeInTheDocument();

      await user.type(screen.getByPlaceholderText("Why you are making this change"), "Trying anyway.");
      expect(screen.getByRole("button", { name: "Save" })).toBeDisabled();
      expect(screen.queryByPlaceholderText("Why this is fine despite the finding")).not.toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/edit")).toEqual([]);
    });

    it("needs an override reason to save past an overridable finding, and sends it", async () => {
      const api = textApi({
        "POST /api/jobs/demo/text/check": {
          segment_id: "D0000-S000001",
          hard: [],
          overridable: [{ category: "naturalness", severity: "medium", message: "reads stiffly" }],
        },
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Hello world.")).getByRole("button", { name: "Edit" }));
      await user.type(editorOf("你好，世界。"), "！");
      expect(await screen.findByText("Needs an override reason:")).toBeInTheDocument();

      await user.type(screen.getByPlaceholderText("Why you are making this change"), "Emphasis.");
      const save = screen.getByRole("button", { name: "Save" });
      expect(save).toBeDisabled();

      await user.type(screen.getByPlaceholderText("Why this is fine despite the finding"), "Deliberate tone.");
      expect(save).toBeEnabled();
      await user.click(save);

      await screen.findByText("Edit saved.");
      expect(api.posted("/api/jobs/demo/text/edit")).toEqual([
        expect.objectContaining({ text: "你好，世界。！", override_reason: "Deliberate tone." }),
      ]);
    });

    it("keeps the editor open and shows the server's reason when a save is refused", async () => {
      textApi({
        "POST /api/jobs/demo/text/edit": jsonResponse(
          { error: "D0000-S000001 has changed since this edit was based on it; reload the segment" },
          422,
        ),
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Hello world.")).getByRole("button", { name: "Edit" }));
      await user.type(screen.getByPlaceholderText("Why you are making this change"), "Clearer wording.");
      await user.click(screen.getByRole("button", { name: "Save" }));

      const toast = await screen.findByRole("status");
      expect(toast).toHaveTextContent("Not saved:");
      expect(toast).toHaveTextContent("has changed since this edit was based on it");
      expect(document.querySelector("textarea.editor")).not.toBeNull();
    });

    it("supports Esc to cancel, Alt+↓ to move, and Ctrl+Enter to save", async () => {
      const api = textApi();
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(screen.getByText("你好，世界。")); // clicking the translation opens the editor
      editorOf("你好，世界。");
      await user.keyboard("{Escape}");
      expect(document.querySelector("textarea.editor")).toBeNull();

      await user.click(screen.getByText("你好，世界。"));
      await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
      const moved = editorOf("嵌套段落。"); // the next row's current (edited) text
      expect(moved).toBeInTheDocument();

      await user.type(screen.getByPlaceholderText("Why you are making this change"), "Keyboard save.");
      await user.click(moved);
      await user.keyboard("{Control>}{Enter}{/Control}");
      await screen.findByText("Edit saved.");
      expect(api.posted("/api/jobs/demo/text/edit")).toEqual([
        expect.objectContaining({ segment_id: "D0000-S000002", expected_event_id: "E000001" }),
      ]);
    });
  });

  describe("history and revert", () => {
    it("shows a segment's events and reverts with a reason and the edit revision", async () => {
      const api = textApi({
        "GET /api/jobs/demo/text/history": {
          segment_id: "D0000-S000002",
          events: [
            {
              event_id: "E000001",
              at: "2026-09-21T10:00:00+08:00",
              author: "minyi",
              action: "edit",
              text: "嵌套段落。",
              previous_text: "嵌套的段落。",
              reason: "Tighter.",
              overrides: [],
            },
          ],
        },
        "POST /api/jobs/demo/text/revert": { event_id: "E000003", action: "revert" },
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Nested paragraph.")).getByRole("button", { name: "History" }));
      expect(await screen.findByText("Tighter.")).toBeInTheDocument();
      expect(screen.getByText(/minyi · 2026-09-21T10:00:00\+08:00/)).toBeInTheDocument();
      expect(api.requested("/api/jobs/demo/text/history?segment=D0000-S000002")).toBe(true);

      const revert = screen.getByRole("button", { name: "Revert to pipeline text" });
      expect(revert).toBeDisabled();
      await user.type(screen.getByPlaceholderText("Reason for reverting (required)"), "Pipeline was right.");
      await user.click(revert);

      expect(await screen.findByText("Reverted to the pipeline translation.")).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/revert")).toEqual([
        { segment_id: "D0000-S000002", reason: "Pipeline was right.", expected_event_id: "E000001" },
      ]);
    });
  });

  describe("resolving a conflict", () => {
    it("shows all three texts and keeps the edit with a reason", async () => {
      const api = textApi({ "POST /api/jobs/demo/text/conflict": { event_id: "E000004", action: "keep" } });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Plain item.")).getByRole("button", { name: "Resolve conflict" }));
      const panel = screen.getByText(/A rerun changed this segment's translation/).closest("td") as HTMLElement;
      expect(within(panel).getByText("我的译文。")).toBeInTheDocument();
      expect(within(panel).getByText("新的流水线译文。")).toBeInTheDocument();
      expect(within(panel).getByText("What changed (based-on text → new pipeline text)")).toBeInTheDocument();

      const resolve = within(panel).getByRole("button", { name: "Resolve conflict" });
      expect(resolve).toBeDisabled();
      await user.click(within(panel).getByRole("radio", { name: "Keep my edit" }));
      expect(resolve).toBeDisabled(); // still needs a reason
      await user.type(within(panel).getByPlaceholderText("Why you're resolving it this way"), "My wording stands.");
      await user.click(resolve);

      expect(await screen.findByText("Kept your edit.")).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/conflict")).toEqual([
        { segment_id: "D0000-S000003", choice: "keep", reason: "My wording stands.", expected_event_id: "E000002" },
      ]);
    });

    it("can take the new pipeline text instead", async () => {
      const api = textApi({ "POST /api/jobs/demo/text/conflict": { event_id: "E000004", action: "take_pipeline" } });
      const user = renderTextTab();
      await screen.findByText("Hello world.");

      await user.click(within(rowOf("Plain item.")).getByRole("button", { name: "Resolve conflict" }));
      const panel = screen.getByText(/A rerun changed this segment's translation/).closest("td") as HTMLElement;
      await user.click(within(panel).getByRole("radio", { name: "Use the new pipeline text" }));
      await user.type(within(panel).getByPlaceholderText("Why you're resolving it this way"), "Rerun improved it.");
      await user.click(within(panel).getByRole("button", { name: "Resolve conflict" }));

      expect(await screen.findByText("Took the new pipeline text.")).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/conflict")).toEqual([
        expect.objectContaining({ choice: "take_pipeline", expected_event_id: "E000002" }),
      ]);
    });
  });

  describe("recompiling", () => {
    it("hides the banner when the compiled book is current", async () => {
      textApi();
      renderTextTab();
      await screen.findByText("Hello world.");
      expect(screen.queryByText(/not in the compiled book/)).not.toBeInTheDocument();
    });

    // Regression: the success toast links to the Progress tab and used to blank the page.
    it("confirms, reruns compile, and links to Progress without crashing", async () => {
      const api = textApi({
        "GET /api/jobs/demo/text": outline({ uncompiled_edit_count: 2 }),
        "POST /api/jobs/demo/rerun": { running: true },
      });
      const user = renderTextTab();
      expect(await screen.findByText(/2 edits not in the compiled book/)).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Recompile" }));
      const dialog = screen.getByRole("alertdialog");
      expect(dialog).toHaveTextContent("Recompile the book?");
      await user.click(within(dialog).getByRole("button", { name: "Recompile" }));

      const link = await screen.findByRole("link", { name: "Follow it on the Progress tab →" });
      expect(link).toHaveAttribute("href", "/jobs/demo/progress");
      expect(screen.queryByText("Something went wrong while showing this page.")).not.toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/rerun")).toEqual([{ stage: "compile" }]);
    });

    it("does nothing when the confirmation is cancelled", async () => {
      const api = textApi({ "GET /api/jobs/demo/text": outline({ uncompiled_edit_count: 1 }) });
      const user = renderTextTab();
      await screen.findByText(/1 edit not in the compiled book/);

      await user.click(screen.getByRole("button", { name: "Recompile" }));
      await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
    });

    it("disables Recompile while the job is running", async () => {
      textApi({
        "GET /api/jobs/demo/info": jobInfo({ running: true, overall: "running" }),
        "GET /api/jobs/demo/text": outline({ uncompiled_edit_count: 1 }),
      });
      renderTextTab();
      await screen.findByText(/1 edit not in the compiled book/);
      expect(screen.getByRole("button", { name: "Recompile" })).toBeDisabled();
    });
  });

  describe("importing XLIFF", () => {
    const importItem = (overrides: Record<string, unknown>) => ({
      segment_id: null,
      document_id: null,
      message: "",
      imported_text: null,
      stale: false,
      edited_since_export: false,
      hard: [],
      overridable: [],
      ...overrides,
    });

    const preview = () => ({
      import_id: "I20260928T101500-abc123",
      file_name: "demo.zh.xlf",
      created_at: "2026-09-28T10:15:00+08:00",
      warnings: [],
      counts: {},
      items: [
        importItem({
          unit_id: "D0000-S000001", segment_id: "D0000-S000001", document_id: "chapter-1",
          category: "import", imported_text: "您好，世界。", current_text: "你好，世界。",
        }),
        importItem({
          unit_id: "D0000-S000002", segment_id: "D0000-S000002", document_id: "chapter-1",
          category: "edited_since_export", edited_since_export: true,
          message: "edited in the Text tab after export", imported_text: "译者的段落。", current_text: "嵌套段落。",
        }),
        importItem({
          unit_id: "D0000-S000003", segment_id: "D0000-S000003", document_id: "chapter-1",
          category: "fails_checks", imported_text: "", current_text: "我的译文。",
          hard: [{ category: "empty", severity: "high", message: "translation is empty" }],
        }),
        importItem({
          unit_id: "D0001-S000001", segment_id: "D0001-S000001", document_id: "chapter-2",
          category: "unchanged", imported_text: "第二章。", current_text: "第二章。",
        }),
        importItem({ unit_id: "D0099-S000001", category: "unknown_id", message: "no such segment in this book" }),
      ],
    });

    const lastImport = () => ({
      import_id: "I20260927T090000-def456",
      file_name: "earlier.xlf",
      applied_at: "2026-09-27T09:00:00+08:00",
      applied: [{ segment_id: "D0000-S000001", document_id: "chapter-1", event_id: "E000010" }],
      skipped: [
        { unit_id: "D0000-S000003", segment_id: "D0000-S000003", document_id: "chapter-1", category: "fails_checks", message: "translation is empty", imported_text: "坏的译文" },
        { unit_id: "D0001-S000001", segment_id: "D0001-S000001", document_id: "chapter-2", category: "stale", message: "the pipeline text changed after export", imported_text: "第二章！" },
      ],
      unchanged_count: 1,
      dropped_at_apply: 0,
    });

    const xliffFile = () => new File(["<xliff/>"], "demo.zh.xlf", { type: "application/xml" });

    async function uploadPreview(api = textApi({ "POST /api/jobs/demo/text/import": preview() })) {
      const user = renderTextTab();
      await screen.findByText("Hello world.");
      await user.upload(screen.getByTestId("xliff-file"), xliffFile());
      await screen.findByRole("region", { name: "Import preview" });
      return { api, user };
    }

    it("puts Import beside Export in the stats card", async () => {
      textApi();
      renderTextTab();
      await screen.findByText("Hello world.");
      const actions = screen.getByRole("link", { name: "⤓ Export XLIFF" }).parentElement as HTMLElement;
      expect(within(actions).getByRole("button", { name: "⤒ Import XLIFF" })).toBeEnabled();
      expect(screen.getByTestId("xliff-file")).toHaveAttribute("accept", ".xlf,.xliff,.xml");
    });

    it("hides Import when the book isn't editable yet", async () => {
      textApi({
        "GET /api/jobs/demo/text": outline({ editable: false }),
        "GET /api/jobs/demo/text/chapter": chapterOne(false),
      });
      renderTextTab();
      await screen.findByText("Hello world.");
      expect(screen.queryByRole("button", { name: "⤒ Import XLIFF" })).not.toBeInTheDocument();
    });

    it("uploads the file and switches the tab into review mode", async () => {
      const { api } = await uploadPreview();

      expect(api.posted("/api/jobs/demo/text/import")).toEqual([{ file_name: "demo.zh.xlf", xliff: "<xliff/>" }]);
      const card = screen.getByRole("region", { name: "Import preview" });
      expect(card).toHaveTextContent("Import preview · demo.zh.xlf");
      const filter = within(card).getByRole("radiogroup", { name: "Import filter" });
      expect(within(filter).getByRole("radio", { name: "Will import 1" })).toHaveAttribute("aria-checked", "true");
      expect(within(filter).getByRole("radio", { name: "Skipped 3" })).toBeInTheDocument();
      expect(within(filter).getByRole("radio", { name: "Unchanged 1" })).toBeInTheDocument();
      expect(within(filter).getByRole("radio", { name: "Not in file 0" })).toBeInTheDocument();
      expect(card).toHaveTextContent("1 unit in the file is not in this book: D0099-S000001");

      // The stats card, view filter and edit actions give way to the review.
      expect(screen.queryByRole("link", { name: "⤓ Export XLIFF" })).not.toBeInTheDocument();
      expect(screen.queryByRole("radiogroup", { name: "Filter segments" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

      // Only the importable row shows, as a diff against the current text.
      expect(screen.getByText("1 of 3 segments")).toBeInTheDocument();
      expect(within(rowOf("Hello world.")).getByText("will import")).toBeInTheDocument();
      expect(within(rowOf("Hello world.")).getByText("您", { selector: "ins" })).toBeInTheDocument();
      const sidebar = screen.getByRole("complementary");
      expect(within(sidebar).getByRole("button", { name: /Chapter One/ })).toHaveTextContent("1 to import");
    });

    it("shows skipped rows with their reasons", async () => {
      const { user } = await uploadPreview();
      await user.click(screen.getByRole("radio", { name: "Skipped 3" }));

      expect(screen.queryByText("Hello world.")).not.toBeInTheDocument();
      expect(within(rowOf("Nested paragraph.")).getByText("edited since export")).toBeInTheDocument();
      expect(within(rowOf("Plain item.")).getByText("fails checks")).toBeInTheDocument();
      expect(within(rowOf("Plain item.")).getByText("translation is empty")).toBeInTheDocument();
    });

    it("updates the counts live as opt-ins change", async () => {
      const { user } = await uploadPreview();
      const card = screen.getByRole("region", { name: "Import preview" });

      // Only opt-ins that would add something are offered.
      expect(within(card).queryByText(/stale segment/)).not.toBeInTheDocument();
      await user.click(within(card).getByRole("checkbox", { name: /1 segment edited in the Text tab after export/ }));

      expect(within(card).getByRole("radio", { name: "Will import 2" })).toBeInTheDocument();
      expect(within(card).getByRole("radio", { name: "Skipped 2" })).toBeInTheDocument();
      expect(within(rowOf("Nested paragraph.")).getByText("will import")).toBeInTheDocument();
      expect(within(screen.getByRole("complementary")).getByRole("button", { name: /Chapter One/ })).toHaveTextContent("2 to import");
    });

    it("needs a reason, then applies with the chosen opt-ins and shows the Last import card", async () => {
      const result = { ...lastImport(), import_id: "I20260928T101500-abc123", file_name: "demo.zh.xlf", skipped: [] };
      const { api, user } = await uploadPreview(textApi({
        "POST /api/jobs/demo/text/import": preview(),
        "POST /api/jobs/demo/text/import/apply": result,
      }));
      const card = screen.getByRole("region", { name: "Import preview" });
      await user.click(within(card).getByRole("checkbox", { name: /edited in the Text tab/ }));

      const apply = within(card).getByRole("button", { name: "Import 2" });
      expect(apply).toBeDisabled();
      await user.type(within(card).getByRole("textbox", { name: "Import reason" }), "Translator pass");
      expect(apply).toBeEnabled();
      await user.click(apply);

      expect(await screen.findByText("Imported 1 segment.")).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/import/apply")).toEqual([{
        import_id: "I20260928T101500-abc123",
        reason: "Translator pass",
        include_stale: false,
        include_edited: true,
        include_overridable: false,
      }]);
      expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
      expect(screen.getByRole("region", { name: "Last import" })).toHaveTextContent("demo.zh.xlf: imported 1, skipped 0");
      expect(screen.getByRole("link", { name: "⤓ Export XLIFF" })).toBeInTheDocument();
    });

    it("keeps Import disabled while the job is running", async () => {
      await uploadPreview(textApi({
        "GET /api/jobs/demo/info": jobInfo({ running: true, overall: "running" }),
        "POST /api/jobs/demo/text/import": preview(),
      }));
      const card = screen.getByRole("region", { name: "Import preview" });
      await userEvent.type(within(card).getByRole("textbox", { name: "Import reason" }), "Translator pass");
      expect(within(card).getByRole("button", { name: "Import 1" })).toBeDisabled();
    });

    it("cancels the preview and returns to the normal view", async () => {
      const { api, user } = await uploadPreview(textApi({
        "POST /api/jobs/demo/text/import": preview(),
        "POST /api/jobs/demo/text/import/cancel": { cancelled: true },
      }));
      await user.click(screen.getByRole("button", { name: "Cancel import" }));

      await waitFor(() => expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/text/import/cancel")).toEqual([{ import_id: "I20260928T101500-abc123" }]);
      expect(screen.getByRole("radiogroup", { name: "Filter segments" })).toBeInTheDocument();
      expect(screen.getByText("3 of 3 segments")).toBeInTheDocument();
    });

    it("resumes a pending preview after a reload", async () => {
      textApi({ "GET /api/jobs/demo/text/import": { pending: preview(), last: null } });
      renderTextTab();
      expect(await screen.findByRole("region", { name: "Import preview" })).toHaveTextContent("demo.zh.xlf");
    });

    it("reports a file the server refuses without entering review mode", async () => {
      textApi({
        "POST /api/jobs/demo/text/import": jsonResponse({ error: "this is XLIFF 1.2; export XLIFF 2.x" }, 400),
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");
      await user.upload(screen.getByTestId("xliff-file"), xliffFile());

      expect(await screen.findByText(/this is XLIFF 1.2/)).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Import preview" })).not.toBeInTheDocument();
    });

    // Regression: error toasts are sticky, so a refused file's error stayed up after
    // the next file was accepted and read as that file's result.
    it("clears an earlier file's error when the next file is accepted", async () => {
      let refuse = true;
      textApi({
        "POST /api/jobs/demo/text/import": () =>
          refuse ? jsonResponse({ error: "the file was exported from a different book" }, 400) : preview(),
      });
      const user = renderTextTab();
      await screen.findByText("Hello world.");
      await user.upload(screen.getByTestId("xliff-file"), new File(["<xliff/>"], "other-book.xlf"));
      expect(await screen.findByText(/different book/)).toBeInTheDocument();

      refuse = false;
      await user.upload(screen.getByTestId("xliff-file"), xliffFile());
      await screen.findByRole("region", { name: "Import preview" });
      expect(screen.queryByText(/different book/)).not.toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Checked demo.zh.xlf.");
    });

    it("refuses a file too large to send", async () => {
      const api = textApi();
      const user = renderTextTab();
      await screen.findByText("Hello world.");
      const file = xliffFile();
      Object.defineProperty(file, "size", { value: 8 * 1024 * 1024 });
      await user.upload(screen.getByTestId("xliff-file"), file);

      expect(await screen.findByText(/too large to import/)).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/text/import")).toEqual([]);
    });

    it("opens a skipped segment from the Last import card, pre-filled, even in another chapter", async () => {
      textApi({ "GET /api/jobs/demo/text/import": { pending: null, last: lastImport() } });
      const user = renderTextTab();
      const card = await screen.findByRole("region", { name: "Last import" });
      expect(card).toHaveTextContent("earlier.xlf: imported 1, skipped 2");
      expect(within(card).getByRole("link", { name: "Download report" })).toHaveAttribute(
        "href", "/api/jobs/demo/text/import/report?import_id=I20260927T090000-def456",
      );

      await user.click(within(card).getByRole("button", { name: "Show skipped ▾" }));
      const [sameChapter, otherChapter] = within(card).getAllByRole("button", { name: "Open" });
      await user.click(sameChapter);
      expect(editorOf("坏的译文")).toBeInTheDocument();

      await user.click(otherChapter);
      expect(await screen.findByDisplayValue("第二章！")).toBeInTheDocument();
      expect(screen.getByText("Second chapter.")).toBeInTheDocument();
    });

    it("dismisses the Last import card", async () => {
      const api = textApi({
        "GET /api/jobs/demo/text/import": { pending: null, last: lastImport() },
        "POST /api/jobs/demo/text/import/dismiss": { dismissed: true },
      });
      const user = renderTextTab();
      const card = await screen.findByRole("region", { name: "Last import" });
      await user.click(within(card).getByRole("button", { name: "Dismiss" }));

      await waitFor(() => expect(screen.queryByRole("region", { name: "Last import" })).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/text/import/dismiss")).toEqual([{ import_id: "I20260927T090000-def456" }]);
    });
  });
});
