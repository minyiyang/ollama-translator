import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { apiError, mockApi } from "../test/mockApi";
import { jobInfo, stage } from "../test/job";

// The glossary gate with a book style sheet (docs/BOOK_CONSISTENCY.md, phase 2).

const entry = { source: "Alice", target: "爱丽丝", note: "常见译名", category: "人名", aliases: [], evidence: [], confidence: 1 };

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
  glossary_pair: { pair: "en-zh", source: "English", target: "Simplified Chinese" },
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

// -- the term list ------------------------------------------------------------

const term = (source: string, target: string, overrides: Record<string, unknown> = {}) => ({
  source, target, note: "", category: "人名", aliases: [], evidence: ["D0000-S000001"], confidence: 1, ...overrides,
});

const ALICE = term("Alice", "爱丽丝", { note: "常见译名", aliases: ["Alice Liddell"] });
const QUEEN = term("Queen of Hearts", "红心王后");
const WONDERLAND = term("Wonderland", "仙境", { category: "地名", evidence: ["D0000-S000001", "D0009-S000404"] });
const RABBIT = term("Rabbit", "兔子", { category: "其他", evidence: [], confidence: 0.7 });
const TERMS = [ALICE, QUEEN, WONDERLAND, RABBIT];

const terms = (overrides: Record<string, unknown> = {}) => ({
  ready: true,
  approve_status: "paused",
  approve_message: "human glossary review required",
  editable: true,
  review_mode: "human",
  entries: TERMS,
  draft_entries: TERMS,
  glossary_pair: { pair: "en-zh", source: "English", target: "Simplified Chinese" },
  approval_records: [],
  approval_summary: {},
  quality: { warnings: ["2 proper nouns from the text are not in the glossary."], suspicious_generic_terms: ["Rabbit"] },
  evidence: { "D0000-S000001": "Alice was beginning to get very tired in Wonderland." },
  categories: ["人名", "地名", "其他"],
  category_labels: { 人名: "Person", 地名: "Place", 其他: "Other" },
  other_category: "其他",
  process: null,
  approval_running: false,
  series_overlay: null,
  ...overrides,
});

const GLOSSARY = "/api/jobs/demo/glossary";
const STORE = "glossary-review:demo";
const WAITING = jobInfo({ overall: "paused", stages: [stage("approve_glossary", "paused")] });

function termsApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/jobs/demo/info": WAITING,
    [`GET ${GLOSSARY}`]: terms(),
    [`POST ${GLOSSARY}/approve`]: { running: true },
    ...overrides,
  });
}

/** Render the tab and wait for the term table. */
async function renderTerms() {
  const user = renderGlossaryTab();
  await screen.findByRole("table");
  return user;
}

const rowOf = (source: string) => screen.getByRole("cell", { name: source }).closest("tr") as HTMLElement;
const shownTerms = () => [...document.querySelectorAll("td.en")].map((cell) => cell.textContent);
const stat = (label: string) => screen.getByText(label, { selector: ".stat span" }).previousElementSibling?.textContent;
const filters = () => within(screen.getByRole("complementary", { name: "Filters" }));
const decide = (user: ReturnType<typeof userEvent.setup>, source: string, label: string) =>
  user.click(within(rowOf(source)).getByRole("radio", { name: label }));
const approved = (api: ReturnType<typeof termsApi>) => api.posted(`${GLOSSARY}/approve`) as { entries?: typeof TERMS; llm?: boolean }[];
const inputs = (source: string) => {
  const [target, note, aliases] = within(rowOf(source)).getAllByRole("textbox");
  return { target, note, aliases, category: within(rowOf(source)).getByRole("combobox") };
};

afterEach(() => vi.useRealTimers());

describe("Glossary tab: before there is a glossary", () => {
  it("tells a draft job to start first and asks for no glossary", async () => {
    const api = termsApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "draft" }) });
    renderGlossaryTab();
    expect(await screen.findByText(/there is no glossary to show/)).toBeInTheDocument();
    expect(api.requested(GLOSSARY)).toBe(false);
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("reports a glossary that cannot be loaded", async () => {
    termsApi({ [`GET ${GLOSSARY}`]: apiError("glossary file is corrupt", 500) });
    renderGlossaryTab();
    expect(await screen.findByText("glossary file is corrupt")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("says the draft is not ready, links to Progress, and looks again every five seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let ready = false;
    const api = termsApi({ [`GET ${GLOSSARY}`]: () => terms({ ready, entries: ready ? TERMS : [], draft_entries: [] }) });
    renderGlossaryTab();
    expect(await screen.findByText(/The glossary draft is not ready yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Watch progress →" })).toHaveAttribute("href", "/jobs/demo/progress");
    expect(screen.queryByRole("complementary", { name: "Filters" })).not.toBeInTheDocument();

    ready = true;
    await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
    expect(await screen.findByRole("table")).toBeInTheDocument();
    expect(screen.queryByText(/The glossary draft is not ready yet/)).not.toBeInTheDocument();
    expect(api.count(GLOSSARY)).toBe(2);
  });
});

describe("Glossary tab: reviewing the draft", () => {
  it("summarizes the draft and lists its warnings", async () => {
    termsApi();
    await renderTerms();
    expect(screen.getByText("waiting for review")).toBeInTheDocument();
    expect(screen.getByText("review mode: human")).toBeInTheDocument();
    expect(["terms", "kept", "deferred", "dropped", "edited", "flagged"].map(stat)).toEqual(["4", "4", "0", "0", "0", "1"]);
    expect(screen.getByText("2 proper nouns from the text are not in the glossary.")).toBeInTheDocument();
    expect(screen.getByText("4 of 4 terms")).toBeInTheDocument();
  });

  it("groups the terms by category under the pair's language names", async () => {
    termsApi();
    await renderTerms();
    expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
      "Decision", "English", "Simplified Chinese", "Category", "Note", "Aliases", "Flags", "Evidence",
    ]);
    expect(screen.getByPlaceholderText("Search English, Simplified Chinese, note, or alias")).toBeInTheDocument();
    const groups = [...document.querySelectorAll("tr.group-head")].map((row) => row.textContent);
    expect(groups).toEqual(["▾Person2", "▾Place1", "▾Other1"]);
    expect(shownTerms()).toEqual(["Alice", "Queen of Hearts", "Wonderland", "Rabbit"]);
  });

  it("shows each term's translation, category, note, aliases, and flags, every one kept by default", async () => {
    termsApi();
    await renderTerms();
    const alice = inputs("Alice");
    expect(alice.target).toHaveValue("爱丽丝");
    expect(alice.target).toHaveAttribute("lang", "zh-CN");
    expect(alice.category).toHaveValue("人名");
    expect(within(alice.category).getAllByRole("option").map((o) => o.textContent)).toEqual(["Person", "Place", "Other"]);
    expect(alice.note).toHaveValue("常见译名");
    expect(alice.aliases).toHaveValue("Alice Liddell");
    expect(within(rowOf("Alice")).getByRole("radio", { name: "✓ Keep" })).toBeChecked();
    expect(rowOf("Alice").querySelectorAll(".flag")).toHaveLength(0);

    expect([...rowOf("Rabbit").querySelectorAll(".flag")].map((flag) => flag.textContent)).toEqual([
      "generic word", "confidence 0.7", "no evidence", "uncategorized",
    ]);
  });

  it("flags terms that share one translation", async () => {
    termsApi({ [`GET ${GLOSSARY}`]: terms({ entries: [ALICE, term("Alys", "爱丽丝")], quality: {} }) });
    await renderTerms();
    expect(within(rowOf("Alice")).getByText("shared translation")).toBeInTheDocument();
    expect(within(rowOf("Alys")).getByText("shared translation")).toBeInTheDocument();
  });

  it("heads the columns English and Chinese when it is not told the book's languages", async () => {
    termsApi({ [`GET ${GLOSSARY}`]: terms({ glossary_pair: null }) });
    await renderTerms();
    expect(screen.getAllByRole("columnheader").slice(1, 3).map((th) => th.textContent)).toEqual(["English", "Chinese"]);
  });

  describe("decisions", () => {
    it("leaves a dropped term out of the approved glossary", async () => {
      const api = termsApi();
      const user = await renderTerms();
      await decide(user, "Rabbit", "✗ Drop");
      expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();
      expect(["kept", "dropped"].map(stat)).toEqual(["3", "1"]);

      await approve(user, "Approve my review & continue");
      expect(approved(api)).toEqual([{ entries: [ALICE, QUEEN, WONDERLAND] }]);
    });

    it("keeps a deferred term as drafted and says so before approving", async () => {
      const api = termsApi();
      const user = await renderTerms();
      await decide(user, "Queen of Hearts", "⏸ Defer");
      expect(["kept", "deferred"].map(stat)).toEqual(["3", "1"]);

      await user.click(screen.getByRole("button", { name: "Approve my review & continue" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Approve your reviewed glossary?");
      expect(dialog).toHaveTextContent("The pipeline continues with this glossary. 1 deferred term is still undecided and will be kept as drafted.");
      await user.click(within(dialog).getByRole("button", { name: "Approve & continue" }));
      await waitFor(() => expect(approved(api)).toHaveLength(1));
      expect(approved(api)[0].entries).toEqual(TERMS);
    });

    it("counts several deferred terms in that warning", async () => {
      termsApi();
      const user = await renderTerms();
      await decide(user, "Queen of Hearts", "⏸ Defer");
      await decide(user, "Alice", "⏸ Defer");
      await user.click(screen.getByRole("button", { name: "LLM review my edits" }));
      expect(await screen.findByRole("alertdialog")).toHaveTextContent("2 deferred terms are still undecided and will be kept as drafted.");
    });

    it("can keep a dropped term again", async () => {
      termsApi();
      const user = await renderTerms();
      await decide(user, "Rabbit", "✗ Drop");
      await decide(user, "Rabbit", "✓ Keep");
      expect(["kept", "dropped"].map(stat)).toEqual(["4", "0"]);
    });

    it("drops every generic word at once, naming them first", async () => {
      const api = termsApi();
      const user = await renderTerms();
      const button = screen.getByRole("button", { name: "✗ Drop 1 generic word" });
      expect(button.parentElement).toHaveTextContent("Rabbit");

      await user.click(button);
      expect(await screen.findByRole("status")).toHaveTextContent("Dropped 1 generic word; they will be translated from context. Keep any of them again to undo.");
      expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();
      expect(screen.queryByRole("button", { name: /generic word/ })).not.toBeInTheDocument();

      await approve(user, "Approve my review & continue");
      expect(approved(api)[0].entries?.map((e) => e.source)).toEqual(["Alice", "Queen of Hearts", "Wonderland"]);
    });

    it("offers no generic-word shortcut when the draft has none", async () => {
      termsApi({ [`GET ${GLOSSARY}`]: terms({ quality: {} }) });
      await renderTerms();
      expect(screen.queryByRole("button", { name: /generic word/ })).not.toBeInTheDocument();
    });
  });

  describe("editing", () => {
    it("sends the edited translation, category, note, and aliases", async () => {
      const api = termsApi();
      const user = await renderTerms();
      const alice = inputs("Alice");
      await user.clear(alice.target);
      await user.type(alice.target, "艾丽斯");
      await user.selectOptions(alice.category, "Place");
      await user.clear(alice.note);
      await user.type(alice.note, "新译名");
      await user.clear(alice.aliases);
      await user.type(alice.aliases, " Ally ,, Miss Alice ");
      expect(stat("edited")).toBe("1");

      await approve(user, "Approve my review & continue");
      expect(approved(api)[0].entries?.[0]).toEqual({ ...ALICE, target: "艾丽斯", category: "地名", note: "新译名", aliases: ["Ally", "Miss Alice"] });
      expect(approved(api)[0].entries?.slice(1)).toEqual([QUEEN, WONDERLAND, RABBIT]);
    });

    it("keeps a term in its drafted category group while its category is edited", async () => {
      termsApi();
      const user = await renderTerms();
      await user.selectOptions(inputs("Alice").category, "Place");
      expect(shownTerms()).toEqual(["Alice", "Queen of Hearts", "Wonderland", "Rabbit"]);
      expect([...document.querySelectorAll("tr.group-head")].map((row) => row.textContent)).toEqual(["▾Person2", "▾Place1", "▾Other1"]);
    });

    it("does not count a dropped term's edits as edited", async () => {
      termsApi();
      const user = await renderTerms();
      await user.type(inputs("Rabbit").target, "先生");
      expect(stat("edited")).toBe("1");
      await decide(user, "Rabbit", "✗ Drop");
      expect(stat("edited")).toBe("0");
    });

    it("remembers unsaved edits in this browser and restores them on the next visit", async () => {
      termsApi();
      const user = await renderTerms();
      await user.type(inputs("Alice").target, "！");
      await decide(user, "Rabbit", "✗ Drop");
      const saved = JSON.parse(localStorage.getItem(STORE) ?? "null");
      expect(saved.edits.find((e: { source: string }) => e.source === "Alice").entry.target).toBe("爱丽丝！");
      expect(saved.edits.find((e: { source: string }) => e.source === "Rabbit").decision).toBe("drop");

      cleanup();
      await renderTerms();
      expect(await screen.findByRole("status")).toHaveTextContent("Restored your unsaved glossary edits from this browser.");
      expect(inputs("Alice").target).toHaveValue("爱丽丝！");
      expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();
      expect(["dropped", "edited"].map(stat)).toEqual(["1", "1"]);
    });

    it("restores edits saved by an older version of the page", async () => {
      localStorage.setItem(STORE, JSON.stringify({
        edits: [
          { english: "Alice", entry: { english: "Alice", chinese: "艾丽斯", note: "", category: "人名", aliases: [], evidence: [], confidence: 1 } },
          { english: "Rabbit", entry: { english: "Rabbit", chinese: "兔子", note: "", category: "其他", aliases: [], evidence: [], confidence: 0.7 }, keep: false },
        ],
      }));
      termsApi();
      await renderTerms();
      expect(inputs("Alice").target).toHaveValue("艾丽斯");
      expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();
      expect(within(rowOf("Queen of Hearts")).getByRole("radio", { name: "✓ Keep" })).toBeChecked();
    });

    it("starts from the draft when the remembered edits are unreadable", async () => {
      localStorage.setItem(STORE, "{not json");
      termsApi();
      await renderTerms();
      expect(inputs("Alice").target).toHaveValue("爱丽丝");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });

    it("stops remembering the edits once the glossary is submitted", async () => {
      termsApi();
      const user = await renderTerms();
      await user.type(inputs("Alice").target, "！");
      expect(localStorage.getItem(STORE)).not.toBeNull();
      await approve(user, "Approve my review & continue");
      await waitFor(() => expect(localStorage.getItem(STORE)).toBeNull());
    });
  });

  describe("finding terms", () => {
    const search = () => screen.getByPlaceholderText(/^Search /);

    it.each([
      ["the source, ignoring case", "wonder", ["Wonderland"]],
      ["the translation", "王后", ["Queen of Hearts"]],
      ["the note", "常见", ["Alice"]],
      ["an alias", "liddell", ["Alice"]],
    ])("searches by %s", async (_what, query, expected) => {
      termsApi();
      const user = await renderTerms();
      await user.type(search(), query);
      expect(shownTerms()).toEqual(expected);
      expect(screen.getByText(`${expected.length} of 4 terms`)).toBeInTheDocument();
    });

    it("says when nothing matches", async () => {
      termsApi();
      const user = await renderTerms();
      await user.type(search(), "no such term");
      expect(screen.getByText("No terms match.")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
      expect(screen.getByText("0 of 4 terms")).toBeInTheDocument();
    });

    it("counts every category and view in the sidebar", async () => {
      termsApi();
      await renderTerms();
      expect(filters().getAllByRole("button").slice(1).map((b) => b.textContent)).toEqual([
        "All categories4", "Person2", "Place1", "Other1",
        "All terms4", "Needs attention1", "Edited0", "Deferred0", "Dropped0",
      ]);
    });

    it("narrows to one category, and back on a second click", async () => {
      termsApi();
      const user = await renderTerms();
      await user.click(filters().getByRole("button", { name: /Person/ }));
      expect(shownTerms()).toEqual(["Alice", "Queen of Hearts"]);
      expect(filters().getByRole("button", { name: /Needs attention/ })).toHaveTextContent("Needs attention0"); // views count within the category

      await user.click(filters().getByRole("button", { name: /Person/ }));
      expect(shownTerms()).toHaveLength(4);

      await user.click(filters().getByRole("button", { name: /Place/ }));
      await user.click(filters().getByRole("button", { name: /All categories/ }));
      expect(shownTerms()).toHaveLength(4);
    });

    it("narrows to the terms that need attention", async () => {
      termsApi();
      const user = await renderTerms();
      await user.click(filters().getByRole("button", { name: /Needs attention/ }));
      expect(shownTerms()).toEqual(["Rabbit"]);
      expect(filters().getByRole("button", { name: /Person/ })).toHaveTextContent("Person0"); // categories count within the view
    });

    it("lists edited, deferred, and dropped terms under their views", async () => {
      termsApi();
      const user = await renderTerms();
      await user.type(inputs("Alice").target, "！");
      await decide(user, "Queen of Hearts", "⏸ Defer");
      await decide(user, "Rabbit", "✗ Drop");

      await user.click(filters().getByRole("button", { name: /Edited/ }));
      expect(shownTerms()).toEqual(["Alice"]);
      await user.click(filters().getByRole("button", { name: /Deferred/ }));
      expect(shownTerms()).toEqual(["Queen of Hearts"]);
      await user.click(filters().getByRole("button", { name: /Dropped/ }));
      expect(shownTerms()).toEqual(["Rabbit"]);
      await user.click(filters().getByRole("button", { name: /All terms/ }));
      expect(shownTerms()).toHaveLength(4);
    });

    it("keeps a term in the list after a decision takes it out of the view, until the list is refreshed", async () => {
      termsApi();
      const user = await renderTerms();
      await decide(user, "Queen of Hearts", "⏸ Defer");
      await user.click(filters().getByRole("button", { name: /Deferred/ }));
      expect(shownTerms()).toEqual(["Queen of Hearts"]);

      await decide(user, "Queen of Hearts", "✓ Keep");
      expect(shownTerms()).toEqual(["Queen of Hearts"]); // it does not jump away under the cursor
      expect(screen.getByText(/1 no longer match this view and stay until you/)).toBeInTheDocument();
      expect(filters().getByRole("button", { name: /Deferred/ })).toHaveTextContent("Deferred0"); // the counts are live

      await user.click(screen.getByRole("button", { name: "Refresh list" }));
      expect(screen.getByText("No terms match.")).toBeInTheDocument();
      expect(screen.queryByText(/no longer match this view/)).not.toBeInTheDocument();
    });

    it("folds one category, all of them, and unfolds them again", async () => {
      termsApi();
      const user = await renderTerms();
      const person = screen.getByRole("button", { name: /▾\s*Person/ });
      await user.click(person);
      expect(person).toHaveAttribute("aria-expanded", "false");
      expect(shownTerms()).toEqual(["Wonderland", "Rabbit"]);

      await user.click(screen.getByRole("button", { name: "Collapse all" }));
      expect(shownTerms()).toEqual([]);
      await user.click(screen.getByRole("button", { name: "Expand all" }));
      expect(shownTerms()).toHaveLength(4);
      expect(person).toHaveAttribute("aria-expanded", "true");
    });
  });

  describe("evidence", () => {
    const evidenceButton = (source: string) => within(rowOf(source)).getAllByRole("button").at(-1) as HTMLElement;

    it("shows the sentences a term was found in, with the term highlighted", async () => {
      termsApi();
      const user = await renderTerms();
      expect(evidenceButton("Alice")).toHaveTextContent("1 ▾");

      await user.click(evidenceButton("Alice"));
      expect(evidenceButton("Alice")).toHaveTextContent("1 ▴");
      const evidence = rowOf("Alice").nextElementSibling as HTMLElement;
      expect(evidence).toHaveTextContent("D0000-S000001Alice was beginning to get very tired in Wonderland.");
      expect(within(evidence).getByText("Alice", { selector: "mark" })).toBeInTheDocument();

      await user.click(evidenceButton("Alice"));
      expect(screen.queryByText(/Alice was beginning to get very tired/)).not.toBeInTheDocument();
    });

    it("says when a sentence's text is not available", async () => {
      termsApi();
      const user = await renderTerms();
      expect(evidenceButton("Wonderland")).toHaveTextContent("2 ▾");
      await user.click(evidenceButton("Wonderland"));
      expect(rowOf("Wonderland").nextElementSibling).toHaveTextContent("D0009-S000404(source text unavailable)");
    });

    it("says when a term has no evidence", async () => {
      termsApi();
      const user = await renderTerms();
      expect(evidenceButton("Rabbit")).toHaveTextContent("0 ▾");
      await user.click(evidenceButton("Rabbit"));
      expect(rowOf("Rabbit").nextElementSibling).toHaveTextContent("No evidence recorded.");
    });
  });

  describe("submitting", () => {
    it("submits nothing when the confirmation is cancelled", async () => {
      const api = termsApi();
      const user = await renderTerms();
      await user.click(screen.getByRole("button", { name: "Approve my review & continue" }));
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(approved(api)).toEqual([]);
    });

    it("sends the edited list for LLM review", async () => {
      const api = termsApi();
      const user = await renderTerms();
      await decide(user, "Rabbit", "✗ Drop");
      await user.click(screen.getByRole("button", { name: "LLM review my edits" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Send your edits to the LLM reviewer?");
      await user.click(within(dialog).getByRole("button", { name: "Send for LLM review" }));
      await waitFor(() => expect(approved(api)).toEqual([{ entries: [ALICE, QUEEN, WONDERLAND], llm: true }]));
    });

    it("warns that an LLM review of the draft discards the edits on the page", async () => {
      const api = termsApi();
      const user = await renderTerms();
      await user.type(inputs("Alice").target, "！");
      await user.click(screen.getByRole("button", { name: "LLM review the draft" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent("Your edits on this page are discarded; the LLM reviews the untouched draft.");
      await user.click(within(dialog).getByRole("button", { name: "LLM review the draft" }));
      await waitFor(() => expect(approved(api)).toEqual([{ llm: true }]));
    });

    it("then shows that the approval is running, what was sent, and no more buttons to press", async () => {
      let running = false;
      const api = termsApi({
        [`GET ${GLOSSARY}`]: () => terms(running ? { approval_running: true, editable: false, approve_status: "running" } : {}),
        [`POST ${GLOSSARY}/approve`]: () => { running = true; return { running: true }; },
      });
      const user = await renderTerms();
      await approve(user, "Approve my review & continue");

      const banner = await screen.findByRole("status");
      expect(banner).toHaveTextContent("Glossary approval is running. Applying your reviewed glossary.");
      expect(banner).toHaveTextContent("The glossary is locked until it finishes; this page updates by itself.");
      expect(within(banner).getByRole("link", { name: "Follow progress →" })).toHaveAttribute("href", "/jobs/demo/progress");
      expect(screen.queryByRole("button", { name: "Approve my review & continue" })).not.toBeInTheDocument();
      expect(screen.queryByRole("radio", { name: "✓ Keep" })).not.toBeInTheDocument();
      expect(api.count("/api/jobs/demo/info")).toBeGreaterThan(1); // the header follows too
    });

    it("shows why the glossary was not submitted and keeps the review", async () => {
      termsApi({ [`POST ${GLOSSARY}/approve`]: apiError("entry 'Alice' has an empty translation", 422) });
      const user = await renderTerms();
      await decide(user, "Rabbit", "✗ Drop");
      await approve(user, "Approve my review & continue");

      const toast = await screen.findByRole("status");
      expect(toast).toHaveTextContent("Not submitted:");
      expect(toast).toHaveTextContent("entry 'Alice' has an empty translation");
      expect(screen.getByRole("button", { name: "Approve my review & continue" })).toBeEnabled();
      expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();
      expect(localStorage.getItem(STORE)).not.toBeNull();
    });
  });
});

describe("Glossary tab: while and after approval", () => {
  it("follows a running approval every three seconds and announces when it completes", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let running = true;
    const api = termsApi({
      [`GET ${GLOSSARY}`]: () => terms(running
        ? { approval_running: true, editable: false, approve_status: "running" }
        : { editable: false, approve_status: "completed" }),
    });
    renderGlossaryTab();
    expect(await screen.findByText("Glossary approval is running.")).toBeInTheDocument();
    expect(shownTerms()).toHaveLength(4); // the locked list is still readable

    running = false;
    await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
    expect(await screen.findByText("Glossary approved. Translation continues on the Progress tab.")).toBeInTheDocument();
    expect(screen.queryByText("Glossary approval is running.")).not.toBeInTheDocument();
    expect(api.count(GLOSSARY)).toBe(2);
  });

  it("says when the approval failed and that the glossary is editable again", async () => {
    termsApi({
      [`GET ${GLOSSARY}`]: terms({ process: { label: "glossary approval", running: false, outcome: "failed", exit_code: 1, output_tail: "RuntimeError: model returned no JSON" } }),
    });
    await renderTerms();
    expect(screen.getByText(/Glossary approval failed \(exit code 1\); your glossary is editable again\./)).toHaveTextContent("RuntimeError: model returned no JSON");
    expect(screen.getByRole("button", { name: "Approve my review & continue" })).toBeEnabled();
  });

  it("points to Progress while another command is running", async () => {
    termsApi({ [`GET ${GLOSSARY}`]: terms({ process: { label: "resume", running: true } }) });
    await renderTerms();
    const banner = screen.getByText(/resume is running\./);
    expect(within(banner).getByRole("link", { name: "Follow progress →" })).toHaveAttribute("href", "/jobs/demo/progress");
  });

  it("explains a series book's synchronized candidate and what approving pins", async () => {
    termsApi({ [`GET ${GLOSSARY}`]: terms({ series_overlay: { series_id: "qel cycle", name: "The Qel Cycle", version: "v002" } }) });
    await renderTerms();
    const banner = screen.getByText(/This book is in series/);
    expect(banner).toHaveTextContent("synchronized to series glossary v002");
    expect(banner).toHaveTextContent("Approving pins this book to v002");
    expect(within(banner).getByRole("link", { name: "The Qel Cycle" })).toHaveAttribute("href", "/series/qel%20cycle");
  });

  describe("an approved glossary", () => {
    const approvedGlossary = () => terms({
      editable: false,
      approve_status: "completed",
      review_mode: "llm",
      entries: [ALICE, { ...QUEEN, target: "红桃王后" }, WONDERLAND],
      draft_entries: TERMS,
      approval_records: [
        { source: "Alice", result: "approved", mode: "deterministic", reasons: [], target: "爱丽丝" },
        { source: "Queen of Hearts", result: "revised", mode: "llm", reasons: ["closer_to_the_source", "common_rendering"], target: "红桃王后" },
      ],
      approval_summary: { deterministic_count: 2, llm_count: 2, revised_count: 1 },
    });

    it("is read-only: no decisions, no inputs, nothing to approve", async () => {
      termsApi({ "GET /api/jobs/demo/info": jobInfo(), [`GET ${GLOSSARY}`]: approvedGlossary() });
      await renderTerms();
      expect(screen.getByText("completed")).toBeInTheDocument();
      expect(screen.getByText("review mode: llm")).toBeInTheDocument();
      expect(screen.queryByRole("radio")).not.toBeInTheDocument();
      expect(screen.getAllByRole("textbox")).toEqual([screen.getByPlaceholderText(/^Search /)]); // the search box is the only input
      expect(screen.queryByRole("button", { name: /Approve|LLM review/ })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /generic word/ })).not.toBeInTheDocument();
      expect(screen.getAllByRole("columnheader").map((th) => th.textContent)).toEqual([
        "English", "Simplified Chinese", "Category", "Note", "Approval", "Evidence",
      ]);
    });

    it("summarizes how the terms were approved", async () => {
      termsApi({ "GET /api/jobs/demo/info": jobInfo(), [`GET ${GLOSSARY}`]: approvedGlossary() });
      await renderTerms();
      expect(["approved terms", "auto-approved", "LLM-reviewed", "revised", "rejected"].map(stat)).toEqual(["3", "2", "2", "1", "—"]);
    });

    it("shows each term's approval record and what the LLM changed", async () => {
      termsApi({ "GET /api/jobs/demo/info": jobInfo(), [`GET ${GLOSSARY}`]: approvedGlossary() });
      await renderTerms();
      const cells = (source: string) => within(rowOf(source)).getAllByRole("cell").map((cell) => cell.textContent);
      expect(cells("Alice")).toEqual(["Alice", "爱丽丝", "Person", "常见译名", "approved deterministic", "1 ▾"]);
      expect(cells("Queen of Hearts")).toEqual(["Queen of Hearts", "红心王后红桃王后", "Person", "", "revised llmcloser to the sourcecommon rendering", "1 ▾"]);
      expect(cells("Wonderland")[4]).toBe("—"); // no record
    });

    it("filters to what the LLM reviewed or changed", async () => {
      termsApi({ "GET /api/jobs/demo/info": jobInfo(), [`GET ${GLOSSARY}`]: approvedGlossary() });
      const user = await renderTerms();
      expect(filters().getAllByRole("button").slice(5).map((b) => b.textContent)).toEqual([
        "All terms3", "Reviewed by LLM1", "Changed by LLM1", "Flagged in draft0",
      ]);
      await user.click(filters().getByRole("button", { name: /Reviewed by LLM/ }));
      expect(shownTerms()).toEqual(["Queen of Hearts"]);
      await user.click(filters().getByRole("button", { name: /Changed by LLM/ }));
      expect(shownTerms()).toEqual(["Queen of Hearts"]);
    });

    it("does not bring back remembered edits once the glossary is approved", async () => {
      localStorage.setItem(STORE, JSON.stringify({ edits: [{ source: "Alice", entry: { ...ALICE, target: "艾丽斯" }, decision: "keep" }] }));
      termsApi({ "GET /api/jobs/demo/info": jobInfo(), [`GET ${GLOSSARY}`]: approvedGlossary() });
      await renderTerms();
      expect(within(rowOf("Alice")).getAllByRole("cell")[1]).toHaveTextContent("爱丽丝");
      expect(screen.queryByRole("status")).not.toBeInTheDocument();
    });
  });
});

// -- journeys -------------------------------------------------------------------
// README: "the job pauses here and the same page becomes an editor: fix a term's
// Chinese, category, note, or aliases, reject it, filter to flagged entries, and read
// each term's source sentences. Approve your version, or send it to the LLM reviewer".

describe("Glossary tab: an editor reviews a book's glossary", () => {
  it("reviews Alice's glossary: reads the evidence for a name, corrects it, drops an ordinary word, and approves", async () => {
    const api = termsApi();
    const user = await renderTerms();

    // Start with what the page says needs attention.
    await user.click(filters().getByRole("button", { name: /Needs attention/ }));
    expect(shownTerms()).toEqual(["Rabbit"]);
    await decide(user, "Rabbit", "✗ Drop");

    // Then check a name against the sentence it came from, and correct it.
    await user.click(filters().getByRole("button", { name: /All terms/ }));
    await user.click(within(rowOf("Queen of Hearts")).getAllByRole("button").at(-1) as HTMLElement);
    expect(rowOf("Queen of Hearts").nextElementSibling).toHaveTextContent("Alice was beginning to get very tired in Wonderland.");
    const queen = inputs("Queen of Hearts");
    await user.clear(queen.target);
    await user.type(queen.target, "红桃王后");
    await user.type(queen.note, "与扑克牌花色一致");

    expect(["kept", "dropped", "edited"].map(stat)).toEqual(["3", "1", "1"]);
    await approve(user, "Approve my review & continue");
    expect(approved(api)).toEqual([{
      entries: [ALICE, { ...QUEEN, target: "红桃王后", note: "与扑克牌花色一致" }, WONDERLAND],
    }]);
  });

  it("is unsure about one term, sets it aside, finds it again by searching, decides, and hands the list to the LLM reviewer", async () => {
    const api = termsApi();
    const user = await renderTerms();
    await decide(user, "Wonderland", "⏸ Defer");
    expect(filters().getByRole("button", { name: /Deferred/ })).toHaveTextContent("Deferred1");

    // Later: look it up by its translation and settle it.
    await user.type(screen.getByPlaceholderText(/^Search /), "仙境");
    expect(shownTerms()).toEqual(["Wonderland"]);
    await user.clear(inputs("Wonderland").target);
    await user.type(inputs("Wonderland").target, "奇境");
    await decide(user, "Wonderland", "✓ Keep");
    expect(stat("deferred")).toBe("0");

    await user.click(screen.getByRole("button", { name: "LLM review my edits" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("The LLM reviews your edited list and may still revise or reject entries; the pipeline then continues.");
    expect(dialog).not.toHaveTextContent("deferred");
    await user.click(within(dialog).getByRole("button", { name: "Send for LLM review" }));
    await waitFor(() => expect(approved(api)).toHaveLength(1));
    expect(approved(api)[0]).toEqual({ llm: true, entries: [ALICE, QUEEN, { ...WONDERLAND, target: "奇境" }, RABBIT] });
  });

  it("closes the browser halfway through, comes back the next day, and finishes from where they stopped", async () => {
    const api = termsApi();
    let user = await renderTerms();
    await decide(user, "Rabbit", "✗ Drop");
    await user.type(inputs("Alice").note, "，全书统一");
    cleanup(); // the tab is closed without approving

    user = await renderTerms();
    expect(await screen.findByRole("status")).toHaveTextContent("Restored your unsaved glossary edits from this browser.");
    expect(inputs("Alice").note).toHaveValue("常见译名，全书统一");
    expect(within(rowOf("Rabbit")).getByRole("radio", { name: "✗ Drop" })).toBeChecked();

    await approve(user, "Approve my review & continue");
    expect(approved(api)[0].entries).toEqual([{ ...ALICE, note: "常见译名，全书统一" }, QUEEN, WONDERLAND]);
  });

  it("reviews the glossary of 阿Q正传 going into Japanese: the columns and inputs follow the book's languages", async () => {
    const AH_Q = term("阿Q", "阿Q", { category: "人名", note: "名前はそのまま" });
    const WEIZHUANG = term("未庄", "未荘", { category: "地名" });
    const api = termsApi({
      "GET /api/jobs/demo/info": jobInfo({ overall: "paused", direction: "zh>ja", stages: [stage("approve_glossary", "paused")] }),
      [`GET ${GLOSSARY}`]: terms({
        entries: [AH_Q, WEIZHUANG], draft_entries: [AH_Q, WEIZHUANG], quality: {},
        glossary_pair: { pair: "zh>ja", source: "Simplified Chinese", target: "Japanese" },
        evidence: { "D0000-S000001": "阿Q没有家，住在未庄的土谷祠里。" },
      }),
    });
    const user = await renderTerms();
    expect(screen.getAllByRole("columnheader").slice(1, 3).map((th) => th.textContent)).toEqual(["Simplified Chinese", "Japanese"]);
    expect(screen.getByPlaceholderText("Search Simplified Chinese, Japanese, note, or alias")).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "未庄" })).toHaveAttribute("lang", "zh-CN");
    expect(inputs("未庄").target).toHaveAttribute("lang", "ja");

    // The place name in its source sentence, then a furigana-friendly rendering.
    await user.click(within(rowOf("未庄")).getAllByRole("button").at(-1) as HTMLElement);
    expect(within(rowOf("未庄").nextElementSibling as HTMLElement).getByText("未庄", { selector: "mark" })).toBeInTheDocument();
    await user.clear(inputs("未庄").target);
    await user.type(inputs("未庄").target, "未荘（ウェイチワン）");

    await approve(user, "Approve my review & continue");
    expect(approved(api)).toEqual([{ entries: [AH_Q, { ...WEIZHUANG, target: "未荘（ウェイチワン）" }] }]);
  });

  it("opens the tab after the LLM approved the glossary and reads what it reviewed and changed", async () => {
    termsApi({
      "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true }),
      [`GET ${GLOSSARY}`]: terms({
        editable: false, approve_status: "completed", review_mode: "llm",
        entries: [ALICE, { ...QUEEN, target: "红桃王后" }, WONDERLAND],
        approval_records: [
          { source: "Alice", result: "approved", mode: "deterministic", reasons: [], target: "爱丽丝" },
          { source: "Queen of Hearts", result: "revised", mode: "llm", reasons: ["matches_the_card_suit"], target: "红桃王后" },
        ],
        approval_summary: { deterministic_count: 2, llm_count: 1, revised_count: 1, rejected_count: 1 },
      }),
    });
    const user = await renderTerms();
    expect(["approved terms", "auto-approved", "LLM-reviewed", "revised", "rejected"].map(stat)).toEqual(["3", "2", "1", "1", "1"]);

    await user.click(filters().getByRole("button", { name: /Changed by LLM/ }));
    expect(shownTerms()).toEqual(["Queen of Hearts"]);
    const cells = within(rowOf("Queen of Hearts")).getAllByRole("cell");
    expect(cells[1]).toHaveTextContent("红心王后红桃王后"); // what was drafted, then what the reviewer made of it
    expect(cells[4]).toHaveTextContent("revised llmmatches the card suit");
    expect(screen.queryByRole("button", { name: /Approve/ })).not.toBeInTheDocument();
  });
});
