import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { mockApi } from "../test/mockApi";

// "The Sign of the Four" as an EPUB with a footnote beside the text, an endnote in a document
// of its own, and a picture: what the title stage settled, as the Text tab is given it.

const counts = { flagged_count: 0, in_review_queue_count: 0, edited_count: 0, conflict_count: 0 };

const outline = {
  available: true,
  editable: true,
  chapters: [
    { document_id: "BOOK", order: -1, title: "Title and contents", segment_count: 1, ...counts, note_count: 0, untranslated_count: 0 },
    { document_id: "chapter", order: 0, title: "The Science of Deduction", segment_count: 3, ...counts, note_count: 1, untranslated_count: 1 },
    { document_id: "endnotes", order: 1, title: "Endnotes", segment_count: 0, ...counts, note_count: 2, untranslated_count: 0 },
  ],
  totals: { documents: 2, segments: 3, flagged: 0, in_review_queue: 0, edited: 0, conflicts: 0, notes: 3, untranslated: 1 },
  uncompiled_edit_count: 0,
};

const row = (overrides: Record<string, unknown>) => ({
  state: "pipeline",
  edit_revision: "",
  findings: [],
  flagged: false,
  in_review_queue: false,
  last_edit: null,
  base_target_sha256: "hash",
  ...overrides,
});

const FOOTNOTE_SOURCE = "<I000>2</I000> Mrs. Hudson kept the house in <I001>Baker Street</I001>.";

const chapter = {
  document_id: "chapter",
  title: "The Science of Deduction",
  order: 0,
  editable: true,
  segments: [
    row({ segment_id: "D0000-S000001", source: "The Science of Deduction", pipeline_text: "演绎法", text: "演绎法", notes: [] }),
    row({
      segment_id: "D0000-S000002",
      source: "Sherlock Holmes took his bottle from the corner of the mantelpiece.<I000>1</I000>",
      pipeline_text: "福尔摩斯从壁炉架的角落里拿起瓶子。<I000>1</I000>",
      text: "福尔摩斯从壁炉架的角落里拿起瓶子。<I000>1</I000>",
      notes: ["D0001-N000002"],
    }),
    row({
      segment_id: "D0000-S000003",
      source: "Mrs. Hudson brought up the tray.<I000>2</I000>",
      pipeline_text: "哈德森太太端上了托盘。<I000>2</I000>",
      text: "哈德森太太端上了托盘。<I000>2</I000>",
      notes: ["D0000-N000001"],
    }),
  ],
  // The model gave the footnote back without its markers: the title stage left it in English.
  notes: [
    row({
      segment_id: "D0000-N000001",
      kind: "note",
      source: FOOTNOTE_SOURCE,
      pipeline_text: FOOTNOTE_SOURCE,
      text: FOOTNOTE_SOURCE,
      untranslated: true,
      flagged: true,
      findings: [{ category: "untranslated", severity: "medium", message: "The title stage left this in the source language." }],
      note: "D0000-N000001",
      referred_from: ["D0000-S000003"],
    }),
  ],
  pictures: [
    row({
      segment_id: "BOOK-Pac64071450ed",
      kind: "description",
      source: "A plan of the sitting-room",
      pipeline_text: "起居室平面图",
      text: "起居室平面图",
      picture_path: "OEBPS/plan.png",
      after: "D0000-S000003",
    }),
  ],
};

const endnotes = {
  document_id: "endnotes",
  title: "Endnotes",
  order: 1,
  editable: true,
  segments: [],
  notes: [
    row({ segment_id: "D0001-N000001", kind: "note", source: "Endnotes", pipeline_text: "尾注", text: "尾注", note: "D0001-N000001", referred_from: [] }),
    row({
      segment_id: "D0001-N000002",
      kind: "note",
      source: "A seven-per-cent solution of cocaine. <I000>Back</I000>",
      pipeline_text: "百分之七的可卡因溶液。<I000>返回</I000>",
      text: "百分之七的可卡因溶液。<I000>返回</I000>",
      note: "D0001-N000002",
      referred_from: ["D0000-S000002"],
    }),
  ],
  pictures: [],
};

const book = {
  document_id: "BOOK",
  title: "Title and contents",
  order: -1,
  editable: true,
  segments: [row({ segment_id: "BOOK-T", kind: "title", source: "The Sign of the Four", pipeline_text: "四签名", text: "四签名" })],
  notes: [],
  pictures: [],
};

function notesApi() {
  return mockApi({
    "GET /api/jobs/demo/info": { job_id: "demo", kind: "job", overall: "complete", source: "sign.epub", config: "demo.yaml", stages: [], running: false, pause_requested: false, can_stop: false, process: null },
    "GET /api/jobs/demo/text": outline,
    "GET /api/jobs/demo/text/chapter": (_body: unknown, url: URL) =>
      ({ BOOK: book, chapter, endnotes })[url.searchParams.get("document_id") as "BOOK" | "chapter" | "endnotes"],
    "POST /api/jobs/demo/text/check": { segment_id: "x", hard: [], overridable: [] },
    "POST /api/jobs/demo/text/edit": { event_id: "E000001", action: "edit" },
    "GET /api/jobs/demo/text/import": { pending: null, last: null },
  });
}

function openText(path = "/jobs/demo/text?chapter=chapter") {
  window.history.pushState({}, "", path);
  const user = userEvent.setup();
  render(<App />);
  return user;
}

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;
const tableRows = () => Array.from(document.querySelectorAll(".text-pairs tbody tr")) as HTMLElement[];

describe("Text tab: a proofreader checks the notes, the pictures, and the title", () => {
  it("reads the footnote under the sentence that refers to it, then goes to the note itself", async () => {
    notesApi();
    const user = openText();
    await screen.findByText("Mrs. Hudson brought up the tray.<I000>2</I000>");

    await user.click(within(rowOf("Mrs. Hudson brought up the tray.<I000>2</I000>")).getByRole("button", { name: "Note 2" }));
    // Shown right under the passage; left in English, it reads the same on both sides.
    const peek = document.querySelector("tr.note-peek") as HTMLElement;
    expect(within(peek).getByText("Note 2")).toBeInTheDocument();
    expect(within(peek).getAllByText(FOOTNOTE_SOURCE)).toHaveLength(2);

    await user.click(within(peek).getByRole("button", { name: "Go to the note" }));
    const note = document.getElementById("row-D0000-N000001") as HTMLElement;
    expect(note).toHaveClass("focus");
    expect(within(note).getByText("Note 2")).toBeInTheDocument();
    expect(within(note).getByRole("button", { name: "↑ D0000-S000003" })).toBeInTheDocument();
  });

  it("follows a reference to an endnote into the endnotes, and back to the sentence", async () => {
    const api = notesApi();
    const user = openText();
    await screen.findByText(/took his bottle/);

    // The endnote is in a document of its own: its chapter is opened at it.
    await user.click(within(rowOf("Sherlock Holmes took his bottle from the corner of the mantelpiece.<I000>1</I000>")).getByRole("button", { name: "Note →" }));
    await screen.findByText("A seven-per-cent solution of cocaine. <I000>Back</I000>");
    expect(api.requested("/api/jobs/demo/text/chapter?document_id=endnotes")).toBe(true);
    await waitFor(() => expect(document.getElementById("row-D0001-N000002")).toHaveClass("focus"));

    await user.click(screen.getByRole("button", { name: "↑ D0000-S000002" }));
    await waitFor(() => expect(document.getElementById("row-D0000-S000002")).toHaveClass("focus"));
  });

  it("sees the picture where it stands in the chapter, with its description to check", async () => {
    notesApi();
    openText();
    const picture = await screen.findByRole("img", { name: "A plan of the sitting-room" });
    expect(picture).toHaveAttribute("src", "/api/jobs/demo/text/picture?path=OEBPS%2Fplan.png");
    const order = tableRows().map((tr) => tr.id);
    // After the passage it follows, and before the notes.
    expect(order.indexOf("row-BOOK-Pac64071450ed")).toBe(order.indexOf("row-D0000-S000003") + 1);
    expect(within(rowOf("A plan of the sitting-room")).getByText("Picture description")).toBeInTheDocument();
  });

  it("finds the footnote the model left in English and translates it, keeping its link and emphasis", async () => {
    const api = notesApi();
    const user = openText();
    await screen.findByText(/took his bottle/);
    expect(within(screen.getByRole("complementary")).getByRole("button", { name: /The Science of Deduction/ })).toHaveTextContent(
      "1 untranslated · 1 note",
    );

    await user.click(screen.getByRole("radio", { name: "Untranslated" }));
    expect(screen.getByText("1 of 5 segments")).toBeInTheDocument();
    const note = document.getElementById("row-D0000-N000001") as HTMLElement;
    expect(within(note).getByText("untranslated")).toBeInTheDocument();

    await user.click(within(note).getByRole("button", { name: "Edit" }));
    expect(screen.getByText(/Keep the markers such as <I000>…<\/I000>/)).toBeInTheDocument();
    const editor = screen.getByDisplayValue(FOOTNOTE_SOURCE);
    await user.clear(editor);
    await user.type(editor, "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。");
    await user.type(screen.getByPlaceholderText("Why you are making this change"), "The model dropped its markers");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() => expect(api.requested("/api/jobs/demo/text/edit")).toBe(true));
    expect(api.posted("/api/jobs/demo/text/edit")[0]).toMatchObject({
      segment_id: "D0000-N000001",
      text: "<I000>2</I000> 哈德森太太在<I001>贝克街</I001>管家。",
      reason: "The model dropped its markers",
    });
  });

  it("corrects the book's title under Title and contents", async () => {
    notesApi();
    const user = openText("/jobs/demo/text");
    // The title and the contents come before the chapters.
    const sidebar = within(await screen.findByRole("complementary"));
    const entries = sidebar.getAllByRole("button").map((button) => button.textContent ?? "").filter((name) => !name.startsWith("«"));
    expect(entries[0]).toMatch(/^Title and contents/);
    await user.click(sidebar.getByRole("button", { name: /Title and contents/ }));
    const title = await screen.findByText("The Sign of the Four");
    expect(within(title.closest("tr") as HTMLElement).getByText("Book title")).toBeInTheDocument();
    expect(within(title.closest("tr") as HTMLElement).getByText("四签名")).toBeInTheDocument();
  });

  it("opens at a note asked for from the review page", async () => {
    notesApi();
    openText("/jobs/demo/text?chapter=endnotes&item=D0001-N000002");
    await screen.findByText("A seven-per-cent solution of cocaine. <I000>Back</I000>");
    await waitFor(() => expect(document.getElementById("row-D0001-N000002")).toHaveClass("focus"));
  });
});
