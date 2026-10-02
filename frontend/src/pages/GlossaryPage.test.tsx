import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { mockApi } from "../test/mockApi";

// The glossary gate with a book style sheet (docs/BOOK_CONSISTENCY.md, phase 2).

const entry = { english: "Alice", chinese: "爱丽丝", note: "常见译名", category: "人名", aliases: [], evidence: [], confidence: 1 };

const draft = {
  characters: [{ name: "Mouse", pronoun: "它", addressed_as: "您", voice: "formal", evidence: [], alternatives: [] }],
  expressions: [{ source: "Off with her head!", rendering: "砍掉她的头！", note: "", evidence: [], alternatives: [] }],
  conventions: { quotation_marks: "“ ”", nested_quotation_marks: "‘ ’", ellipsis: "……", dash: "——", numerals: "" },
};

const glossary = (review: "human" | "glossary") => ({
  ready: true,
  approve_status: "paused",
  approve_message: review === "human" ? "style sheet review required" : "human glossary review required",
  editable: true,
  review_mode: "",
  entries: [entry],
  draft_entries: [entry],
  approval_records: [],
  approval_summary: {},
  quality: {},
  evidence: {},
  categories: ["人名"],
  category_labels: { 人名: "Person" },
  other_category: "其他",
  process: null,
  approval_running: false,
  series_overlay: null,
  style_sheet: { draft, approved: null, review, pronouns: ["他", "她", "它"], addresses: ["你", "您"] },
});

function glossaryApi(review: "human" | "glossary") {
  return mockApi({
    "GET /api/jobs/demo/info": {
      job_id: "demo", kind: "job", overall: "paused", source: "book.epub", config: "demo.yaml",
      stages: [], running: false, pause_requested: false, can_stop: false, process: null,
    },
    "GET /api/jobs/demo/glossary": glossary(review),
    "POST /api/jobs/demo/glossary/approve": { running: true },
  });
}

async function approve(user: ReturnType<typeof userEvent.setup>, button: string) {
  await user.click(await screen.findByRole("button", { name: button }));
  const dialog = await screen.findByRole("alertdialog");
  await user.click(within(dialog).getAllByRole("button").at(-1) as HTMLElement);
}

function renderGlossaryTab() {
  window.history.pushState({}, "", "/jobs/demo/glossary");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

describe("Glossary tab: style-sheet review gate", () => {
  it("asks for a human review and sends the sheet as shown with any approval", async () => {
    const api = glossaryApi("human");
    const user = renderGlossaryTab();
    const section = await screen.findByRole("region", { name: "Style sheet" });
    expect(within(section).getByText(/Waiting for your review/)).toBeInTheDocument();
    expect(within(section).getByText(/Character notes are context only/)).toBeInTheDocument();

    // Untouched, the LLM glossary review still carries the human-reviewed style sheet.
    await approve(user, "LLM review the draft");
    expect(api.posted("/api/jobs/demo/glossary/approve")).toEqual([{ llm: true, style: draft }]);
  });

  it("sends the reviewer's edits", async () => {
    const api = glossaryApi("human");
    const user = renderGlossaryTab();
    await screen.findByRole("region", { name: "Style sheet" });
    await user.selectOptions(screen.getByRole("combobox", { name: "Pronoun for Mouse" }), "他");

    await approve(user, "Approve my review & continue");
    const [body] = api.posted("/api/jobs/demo/glossary/approve") as { style: typeof draft }[];
    expect(body.style.characters[0].pronoun).toBe("他");
  });

  it("follows the glossary's review when set to, sending the sheet only if edited", async () => {
    const api = glossaryApi("glossary");
    const user = renderGlossaryTab();
    const section = await screen.findByRole("region", { name: "Style sheet" });
    expect(within(section).queryByText(/Waiting for your review/)).not.toBeInTheDocument();

    await approve(user, "LLM review the draft");
    expect(api.posted("/api/jobs/demo/glossary/approve")).toEqual([{ llm: true }]);
  });
});
