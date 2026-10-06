import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DialogProvider } from "../components/Dialog";
import { ToastProvider } from "../components/Toast";
import type { Term } from "../lib/series";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { WorkbenchTab, type SeriesProcess } from "./SeriesWorkbench";

// -- fixtures -----------------------------------------------------------------

const term = (term_id: string, source: string, target: string, patch: Partial<Term> = {}): Term => ({
  term_id, source, target, category: "person", origin: "consensus", books: {}, decision: "keep",
  decided_by: "rule", reason: "", locked_from: null, suggestion: null, ...patch,
});

const QELMAR = term("T1", "Qelmar", "凯尔玛", { books: { b1: ["凯尔玛"], b2: ["凯尔玛"] } });
const ARDEN = term("T2", "Arden", "阿登", {
  category: "place", origin: "conflict", decision: "pending", books: { b1: ["阿登"], b2: ["雅顿"] }, reason: "The books disagree.",
});
const HUDSON = term("T3", "Hudson", "哈德森", { origin: "single_book", decision: "drop", books: { b2: ["哈德森"] }, mentions: { b1: 4, b2: 9 } });
const ANCHOR = term("T4", "Anchor", "锚", {
  category: "item", books: { b1: ["锚"], b2: ["锚"] },
  suggestion: { kind: "drop_generic", target: null, rationale: "An ordinary noun.", model: "qwen3" },
});
const SWORD = term("T5", "Sunblade", "圣剑", { category: "item", origin: "carried", decided_by: "user", locked_from: "v001", books: { b1: ["圣剑"] } });
const TERMS = [QELMAR, ARDEN, HUDSON, ANCHOR, SWORD];

const workbench = (overrides: Record<string, unknown> = {}) => ({
  series_id: "qel",
  glossary_pair: { pair: "en-zh", source: "English", target: "Simplified Chinese" },
  based_on: "v001",
  built_at: "2026-09-28T10:00:00Z",
  not_ready: [],
  terms: TERMS,
  books: ["b1", "b2"],
  next_version: "v002",
  latest: "v001",
  publish: { keep: 3, pending: 1, added: ["Qelmar", "Anchor"], changed: [], removed: [], stale: false },
  category_labels: { person: "Person", place: "Place", item: "Item" },
  book_info: [{ job_id: "b1", volume: 1, title: "Book One" }, { job_id: "b2", volume: 2, title: "Book Two" }],
  ...overrides,
});

const BASE = "/api/series/qel";

function workbenchApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    [`GET ${BASE}/workbench`]: workbench(),
    [`POST ${BASE}/workbench/decide`]: workbench(),
    [`POST ${BASE}/workbench/suggestions`]: workbench(),
    [`POST ${BASE}/suggest`]: { started: true },
    [`POST ${BASE}/publish`]: { version: "v002" },
    [`GET ${BASE}/log`]: { file: null, lines: [] },
    ...overrides,
  });
}

async function renderWorkbench(process: SeriesProcess = null) {
  const onChanged = vi.fn();
  const onPublished = vi.fn();
  const user = userEvent.setup();
  render(
    <ToastProvider>
      <DialogProvider>
        <WorkbenchTab seriesId="qel" process={process} onChanged={onChanged} onPublished={onPublished} />
      </DialogProvider>
    </ToastProvider>,
  );
  await screen.findByText(/candidate terms/);
  return { user, onChanged, onPublished };
}

const sidebar = () => within(screen.getByRole("complementary", { name: "Views" }));
const view = (name: RegExp | string) => sidebar().getByRole("button", { name });
const card = (source: string) => screen.getByText(source, { selector: "b" }).closest("section") as HTMLElement;
const shownTerms = () => screen.queryAllByRole("checkbox", { name: /^Select / }).map((box) => box.getAttribute("aria-label")?.replace("Select ", ""));
const posted = (api: ReturnType<typeof workbenchApi>, path: string) => api.posted(`${BASE}/${path}`);

/** Open a term's editor from the view that lists it. */
async function decideOn(user: ReturnType<typeof userEvent.setup>, source: string, viewName: RegExp | string = /All terms/) {
  await user.click(view(viewName));
  await user.click(within(card(source)).getByRole("button", { name: "Decide" }));
  return within(card(source));
}

const KEEP_REASON = "Canonical form for the series.";
const DROP_REASON = "Generic word, not a glossary term.";

afterEach(() => vi.useRealTimers());

// -- tests --------------------------------------------------------------------

describe("Series workbench", () => {
  describe("loading", () => {
    it("says so while loading", async () => {
      const gate = deferred();
      workbenchApi({ [`GET ${BASE}/workbench`]: () => gate.promise });
      render(<ToastProvider><DialogProvider><WorkbenchTab seriesId="qel" process={null} onChanged={() => {}} onPublished={() => {}} /></DialogProvider></ToastProvider>);
      expect(screen.getByText("Loading…")).toBeInTheDocument();
      gate.resolve(workbench());
      expect(await screen.findByText(/candidate terms/)).toBeInTheDocument();
    });

    it("reports a workbench that cannot be loaded", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: apiError("no candidate has been built", 404) });
      render(<ToastProvider><DialogProvider><WorkbenchTab seriesId="qel" process={null} onChanged={() => {}} onPublished={() => {}} /></DialogProvider></ToastProvider>);
      expect(await screen.findByText("no candidate has been built")).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    });

    it("summarizes the candidate and names the books left out", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ not_ready: ["b3", "b4"] }) });
      await renderWorkbench();
      expect(screen.getByText(/candidate terms/)).toHaveTextContent("5 candidate terms on top of v001 · 3 to publish · 1 pending");
      expect(screen.getByText("Not included yet (glossary not ready): b3, b4.")).toBeInTheDocument();
    });

    it("leaves out the base version and the pending chip of a first, fully decided candidate", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ based_on: null, publish: { keep: 3, pending: 0, added: [], changed: [], removed: [], stale: false } }) });
      await renderWorkbench();
      expect(screen.getByText(/candidate terms/)).toHaveTextContent(/^5 candidate terms · 3 to publish$/);
      expect(screen.queryByText(/Not included yet/)).not.toBeInTheDocument();
    });
  });

  describe("views", () => {
    it("starts on the terms that need a decision, with a count on every view", async () => {
      workbenchApi();
      await renderWorkbench();
      expect(shownTerms()).toEqual(["Arden"]);
      expect(sidebar().getAllByRole("button").slice(1).map((b) => b.textContent)).toEqual([
        "LLM suggestions1", "Needs a decision1", "Conflicts1", "To publish3", "Single-book terms1",
        "Vol. 1 · Book One0", "Vol. 2 · Book Two1",
        "Found in other books1", "Already published1", "Dropped1", "All terms5",
      ]);
    });

    it.each([
      [/To publish/, ["Qelmar", "Anchor", "Sunblade"]],
      [/Conflicts/, ["Arden"]],
      [/Single-book terms/, ["Hudson"]],
      [/Already published/, ["Sunblade"]],
      [/Dropped/, ["Hudson"]],
      [/All terms/, ["Qelmar", "Arden", "Hudson", "Anchor", "Sunblade"]],
    ])("lists the terms of %s", async (name, expected) => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(name));
      expect(shownTerms()).toEqual(expected);
    });

    it("lists one book's own terms, and says when a view is empty", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/Book Two/));
      expect(shownTerms()).toEqual(["Hudson"]);

      await user.click(view(/Book One/));
      expect(shownTerms()).toEqual([]);
      expect(screen.getByText("No terms in this view.")).toBeInTheDocument();
    });

    it("explains the terms found in other books and flags how many books mention them", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/Found in other books/));
      expect(screen.getByText(/Each term below is in/)).toHaveTextContent("one book's glossary only");
      expect(within(card("Hudson")).getByText("in 2 books’ text")).toBeInTheDocument();
    });

    it("searches the current view by source, ignoring case, or by translation", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/All terms/));
      const search = screen.getByPlaceholderText("Search terms");

      await user.type(search, "  qEl ");
      expect(shownTerms()).toEqual(["Qelmar"]);

      await user.clear(search);
      await user.type(search, "圣剑");
      expect(shownTerms()).toEqual(["Sunblade"]);

      await user.clear(search);
      await user.type(search, "no such term");
      expect(screen.getByText("No terms in this view.")).toBeInTheDocument();
    });

    it("finds a translation written in Latin letters whatever its capitals", async () => {
      const zhEn = workbench({
        glossary_pair: { pair: "zh-en", source: "Simplified Chinese", target: "English" },
        terms: [term("Q1", "阿Q", "Ah Q", { decision: "pending" }), term("Q2", "未庄", "Weizhuang", { decision: "pending" })],
      });
      workbenchApi({ [`GET ${BASE}/workbench`]: zhEn });
      const { user } = await renderWorkbench();
      await user.type(screen.getByPlaceholderText("Search terms"), "weiZHUANG");
      expect(shownTerms()).toEqual(["未庄"]);
    });

    it("names books and categories by their ids when it has no better names for them", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ book_info: undefined, category_labels: undefined, glossary_pair: undefined }) });
      await renderWorkbench();
      expect(view(/^b1 · b1/)).toBeInTheDocument();
      expect(within(card("Arden")).getByText("place")).toBeInTheDocument();
    });
  });

  describe("a term", () => {
    it("shows its translation, category, origin, decision, and each book's rendering", async () => {
      workbenchApi();
      await renderWorkbench();
      const arden = within(card("Arden"));
      expect(arden.getByText("阿登", { selector: ".term-zh" })).toHaveAttribute("lang", "zh-CN");
      expect(arden.getByText("Arden")).toHaveAttribute("lang", "en");
      expect(arden.getByText("Place")).toBeInTheDocument();
      expect(arden.getByText("conflict")).toBeInTheDocument();
      expect(arden.getByText("pending")).toBeInTheDocument();
      expect(arden.getByText("The books disagree.")).toBeInTheDocument();
      expect(arden.getByTitle("Book One (b1)")).toHaveTextContent("Vol. 1 阿登");
      expect(arden.getByTitle("Book Two (b2)")).toHaveTextContent("Vol. 2 雅顿");
    });

    it("marks a published term and a decision made by a person", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/Already published/));
      const sword = within(card("Sunblade"));
      expect(sword.getByText("published in v001")).toBeInTheDocument();
      expect(sword.getByText("keep · by you")).toBeInTheDocument();
      expect(sword.queryByText("published", { exact: true })).not.toBeInTheDocument(); // no origin chip for carried terms
      expect(sword.getByTitle("Book Two (b2)")).toHaveTextContent("Vol. 2 —");
    });

    it("says where a single-book term is only mentioned, and how often", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/Dropped/));
      const hudson = within(card("Hudson"));
      expect(hudson.getByText("single book")).toBeInTheDocument();
      expect(hudson.getByTitle("Book One (b1)")).toHaveTextContent("Vol. 1 not in glossary · 4×");
      expect(hudson.getByTitle("Book Two (b2)")).toHaveTextContent("Vol. 2 哈德森 · 9×");
    });
  });

  describe("evidence", () => {
    const evidence = {
      source: "Arden",
      books: [
        {
          job_id: "b1", volume: 1, title: "Book One", mentions: 1200, snippets: ["ignored: the glossary has evidence"],
          glossary: [{ target: "阿登", category: "place", note: "a forest", evidence: ["They rode into arden at dusk."] }],
        },
        { job_id: "b2", volume: null, title: "Book Two", mentions: 0, snippets: ["Beyond Arden lay the sea."], glossary: [] },
      ],
    };

    it("loads each book's glossary entry, mentions, and passages on demand", async () => {
      const api = workbenchApi({ [`GET ${BASE}/term`]: evidence });
      const { user } = await renderWorkbench();
      await user.click(within(card("Arden")).getByRole("button", { name: "Evidence" }));

      const panel = (await within(card("Arden")).findByText(/mentions in the text/)).closest(".evidence") as HTMLElement;
      expect(api.requested(`${BASE}/term?id=T2`)).toBe(true);
      const [one, two] = [...panel.children] as HTMLElement[];
      expect(one).toHaveTextContent("Vol. 1 Book One b1 · 1200+ mentions in the text");
      expect(one).toHaveTextContent("Glossary: 阿登 Place a forest");
      expect(within(one).getByText("arden", { selector: "mark" })).toBeInTheDocument(); // the term is highlighted in its sentence
      expect(one).not.toHaveTextContent("ignored: the glossary has evidence");

      expect(two).toHaveTextContent("b2 Book Two b2 · not mentioned in the text");
      expect(two).toHaveTextContent("Not in this book's glossary.");
      expect(within(two).getByText("Arden", { selector: "mark" }).parentElement).toHaveTextContent("Beyond Arden lay the sea.");
    });

    it("hides the evidence again", async () => {
      workbenchApi({ [`GET ${BASE}/term`]: evidence });
      const { user } = await renderWorkbench();
      await user.click(within(card("Arden")).getByRole("button", { name: "Evidence" }));
      await within(card("Arden")).findByText(/mentions in the text/);

      await user.click(within(card("Arden")).getByRole("button", { name: "Hide evidence" }));
      expect(within(card("Arden")).queryByText(/mentions in the text/)).not.toBeInTheDocument();
      expect(within(card("Arden")).getByRole("button", { name: "Evidence" })).toBeInTheDocument();
    });

    it("says so while loading and when the evidence cannot be loaded", async () => {
      const gate = deferred();
      workbenchApi({ [`GET ${BASE}/term`]: () => gate.promise });
      const { user } = await renderWorkbench();
      await user.click(within(card("Arden")).getByRole("button", { name: "Evidence" }));
      expect(within(card("Arden")).getByText("Loading evidence…")).toBeInTheDocument();

      gate.resolve(apiError("book b2 has no decompiled text", 500));
      expect(await within(card("Arden")).findByText("book b2 has no decompiled text")).toBeInTheDocument();
    });
  });

  describe("deciding one term", () => {
    it("needs a reason before anything can be saved", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      expect(editor.getByText("Pick or write a reason first.")).toBeInTheDocument();
      for (const name of ["Save changes", "Keep", "Drop", "Leave pending"]) expect(editor.getByRole("button", { name })).toBeDisabled();

      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      expect(editor.queryByText("Pick or write a reason first.")).not.toBeInTheDocument();
      for (const name of ["Keep", "Drop", "Leave pending"]) expect(editor.getByRole("button", { name })).toBeEnabled();
      expect(editor.getByRole("button", { name: "Save changes" })).toBeDisabled(); // nothing changed yet
    });

    it("keeps a term as it is, with the reason", async () => {
      const kept = workbench({ terms: TERMS.map((t) => (t === ARDEN ? { ...ARDEN, decision: "keep", decided_by: "user" } : t)) });
      const api = workbenchApi({ [`POST ${BASE}/workbench/decide`]: kept });
      const { user, onChanged } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      await user.click(editor.getByRole("button", { name: "Keep" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Saved: 1 term kept.");
      expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T2"], decision: "keep", reason: KEEP_REASON, target: null, category: null, unlock: false },
      ]);
      expect(onChanged).toHaveBeenCalledOnce();
      // The server's answer replaces the list, and the editor closes.
      expect(within(card("Arden")).getByText("keep · by you")).toBeInTheDocument();
      expect(within(card("Arden")).queryByRole("textbox", { name: "Series translation" })).not.toBeInTheDocument();
    });

    it("keeps a term with the rendering picked from another book", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      expect(editor.getByRole("textbox", { name: "Series translation" })).toHaveValue("阿登");

      await user.click(editor.getByRole("button", { name: "雅顿" }));
      expect(editor.getByRole("textbox", { name: "Series translation" })).toHaveValue("雅顿");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      await user.click(editor.getByRole("button", { name: "Keep with these changes" }));

      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T2"], decision: "keep", reason: KEEP_REASON, target: "雅顿", category: null, unlock: false },
      ]));
    });

    it("saves a typed rendering without changing the decision", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      const rendering = editor.getByRole("textbox", { name: "Series translation" });
      await user.clear(rendering);
      await user.type(rendering, " 亚登 ");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      await user.click(editor.getByRole("button", { name: "Save changes" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Saved: 1 term left pending.");
      expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T2"], decision: "pending", reason: KEEP_REASON, target: "亚登", category: null, unlock: false },
      ]);
    });

    it("saves a new category on its own", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      const category = editor.getByRole("combobox", { name: "Category" });
      expect(category).toHaveValue("place");
      expect(within(category).getAllByRole("option").map((o) => o.textContent)).toEqual(["Person", "Place", "Item"]);

      await user.selectOptions(category, "Person");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      await user.click(editor.getByRole("button", { name: "Save changes" }));
      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T2"], decision: "pending", reason: KEEP_REASON, target: null, category: "person", unlock: false },
      ]));
    });

    it("cannot keep a term with an empty rendering, but can still drop it", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.clear(editor.getByRole("textbox", { name: "Series translation" }));
      await user.click(editor.getByRole("radio", { name: DROP_REASON }));
      expect(editor.getByRole("button", { name: "Keep with these changes" })).toBeDisabled();
      expect(editor.getByRole("button", { name: "Save changes" })).toBeDisabled();
      expect(editor.getByRole("button", { name: "Drop" })).toBeEnabled();
    });

    it("drops a term, and an edit made to its translation is dropped with it", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.click(editor.getByRole("button", { name: "雅顿" }));
      await user.click(editor.getByRole("radio", { name: DROP_REASON }));
      await user.click(editor.getByRole("button", { name: "Drop" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Saved: 1 term dropped.");
      expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T2"], decision: "drop", reason: DROP_REASON, target: null, category: null, unlock: false },
      ]);
    });

    it("puts a decided term back to pending", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Qelmar");
      await user.click(editor.getByRole("radio", { name: "The books agree after review." }));
      await user.click(editor.getByRole("button", { name: "Leave pending" }));
      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([
        expect.objectContaining({ term_ids: ["T1"], decision: "pending", reason: "The books agree after review." }),
      ]));
    });

    it("accepts a custom reason of at least three characters", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      const custom = editor.getByPlaceholderText("Describe why (at least 3 characters)");

      await user.type(custom, "ok");
      expect(editor.getByRole("radio", { name: "Custom reason" })).toBeChecked();
      expect(editor.getByRole("button", { name: "Keep" })).toBeDisabled();

      await user.type(custom, "ay, per the author");
      await user.click(editor.getByRole("button", { name: "Keep" }));
      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([expect.objectContaining({ reason: "okay, per the author" })]));
    });

    it("switches from a custom reason back to a preset", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.type(editor.getByPlaceholderText("Describe why (at least 3 characters)"), "my own reason");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      expect(editor.getByRole("radio", { name: "Custom reason" })).not.toBeChecked();

      await user.click(editor.getByRole("button", { name: "Keep" }));
      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([expect.objectContaining({ reason: KEEP_REASON })]));
    });

    it("changes a published term only when told to", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Sunblade");
      const unlock = editor.getByRole("checkbox", { name: "change the term published in v001" });
      expect(unlock).not.toBeChecked();

      await user.click(unlock);
      await user.click(editor.getByRole("radio", { name: DROP_REASON }));
      await user.click(editor.getByRole("button", { name: "Drop" }));
      await waitFor(() => expect(posted(api, "workbench/decide")).toEqual([expect.objectContaining({ term_ids: ["T5"], decision: "drop", unlock: true })]));
    });

    it("has no unlock option on a term that was never published", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      expect(editor.queryByRole("checkbox", { name: /change the term published/ })).not.toBeInTheDocument();
    });

    it("closes the editor without saving", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.click(editor.getByRole("button", { name: "Close" }));
      expect(editor.queryByRole("textbox", { name: "Series translation" })).not.toBeInTheDocument();
      expect(posted(api, "workbench/decide")).toEqual([]);
    });

    it("shows why a decision was refused and keeps the editor open", async () => {
      workbenchApi({ [`POST ${BASE}/workbench/decide`]: apiError("T2 is locked by v001; tick the unlock box", 409) });
      const { user, onChanged } = await renderWorkbench();
      const editor = await decideOn(user, "Arden");
      await user.click(editor.getByRole("radio", { name: KEEP_REASON }));
      await user.click(editor.getByRole("button", { name: "Keep" }));

      expect(await screen.findByRole("status")).toHaveTextContent("T2 is locked by v001; tick the unlock box");
      expect(editor.getByRole("textbox", { name: "Series translation" })).toBeInTheDocument();
      expect(editor.getByRole("button", { name: "Keep" })).toBeEnabled();
      expect(onChanged).not.toHaveBeenCalled();
    });
  });

  describe("deciding several terms", () => {
    const bulk = () => within(screen.getByText("selected").closest("section") as HTMLElement);
    const tick = async (user: ReturnType<typeof userEvent.setup>, ...sources: string[]) => {
      for (const source of sources) await user.click(screen.getByRole("checkbox", { name: `Select ${source}` }));
    };

    it("keeps the ticked terms with one reason, in the order they were ticked", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/All terms/));
      await tick(user, "Hudson", "Qelmar");

      expect(bulk().getByText("2")).toBeInTheDocument();
      expect(bulk().getByText("Pick or write a reason for all selected terms.")).toBeInTheDocument();
      expect(bulk().getByRole("button", { name: "Keep" })).toBeDisabled();
      expect(bulk().getByRole("button", { name: "Drop" })).toBeDisabled();

      await user.click(bulk().getByRole("radio", { name: KEEP_REASON }));
      await user.click(bulk().getByRole("button", { name: "Keep" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Saved: 2 terms kept.");
      expect(posted(api, "workbench/decide")).toEqual([
        { term_ids: ["T3", "T1"], decision: "keep", reason: KEEP_REASON, target: null, category: null, unlock: false },
      ]);
      expect(screen.queryByText("selected")).not.toBeInTheDocument(); // the selection is cleared
    });

    it("drops the ticked terms", async () => {
      const api = workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/All terms/));
      await tick(user, "Anchor");
      await user.click(bulk().getByRole("radio", { name: DROP_REASON }));
      await user.click(bulk().getByRole("button", { name: "Drop" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Saved: 1 term dropped.");
      expect(posted(api, "workbench/decide")).toEqual([expect.objectContaining({ term_ids: ["T4"], decision: "drop", reason: DROP_REASON })]);
    });

    it("unticks one term, clears the selection, and forgets it when the view changes", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/All terms/));
      await tick(user, "Hudson", "Qelmar");
      await tick(user, "Hudson");
      expect(bulk().getByText("1")).toBeInTheDocument();

      await user.click(bulk().getByRole("button", { name: "Clear" }));
      expect(screen.queryByText("selected")).not.toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: "Select Qelmar" })).not.toBeChecked();

      await tick(user, "Qelmar");
      await user.click(view(/To publish/));
      expect(screen.queryByText("selected")).not.toBeInTheDocument();
      expect(screen.getByRole("checkbox", { name: "Select Qelmar" })).not.toBeChecked();
    });
  });

  describe("LLM suggestions", () => {
    const withSuggestion = (base: Term, kind: "resolve" | "promote", target: string | null) =>
      workbench({ terms: [{ ...base, suggestion: { kind, target, rationale: "Because.", model: "qwen3" } }] });

    it("shows what the LLM suggests and why", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(view(/LLM suggestions/));
      expect(shownTerms()).toEqual(["Anchor"]);
      expect(within(card("Anchor")).getByText(/LLM suggests:/).parentElement).toHaveTextContent(
        "LLM suggests: drop it: an ordinary word, not a series term. An ordinary noun.",
      );
    });

    it("puts a suggested translation into a sentence", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: withSuggestion(ARDEN, "resolve", "雅顿") });
      await renderWorkbench();
      expect(within(card("Arden")).getByText(/LLM suggests:/).parentElement).toHaveTextContent("use 雅顿 for the whole series. Because.");
    });

    it("puts a suggested promotion into a sentence", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: withSuggestion(HUDSON, "promote", null) });
      const { user } = await renderWorkbench();
      await user.click(view(/All terms/));
      expect(within(card("Hudson")).getByText(/LLM suggests:/).parentElement).toHaveTextContent("promote it to the series glossary. Because.");
    });

    it.each([["Accept", true], ["Reject", false]])("answers one suggestion: %s", async (label, accept) => {
      const answered = workbench({ terms: TERMS.map((t) => (t === ANCHOR ? { ...ANCHOR, suggestion: null } : t)) });
      const api = workbenchApi({ [`POST ${BASE}/workbench/suggestions`]: answered });
      const { user, onChanged } = await renderWorkbench();
      await user.click(view(/To publish/));
      await user.click(within(card("Anchor")).getByRole("button", { name: label }));

      await waitFor(() => expect(within(card("Anchor")).queryByText(/LLM suggests:/)).not.toBeInTheDocument());
      expect(posted(api, "workbench/suggestions")).toEqual([{ term_ids: ["T4"], accept }]);
      expect(onChanged).toHaveBeenCalledOnce();
    });

    it.each([["Accept all shown", true], ["Reject all shown", false]])("answers every suggestion in view: %s", async (label, accept) => {
      const all = workbench({
        terms: [ANCHOR, { ...ARDEN, suggestion: { kind: "resolve", target: "雅顿", rationale: "More common.", model: "qwen3" } }, QELMAR],
      });
      const api = workbenchApi({ [`GET ${BASE}/workbench`]: all });
      const { user } = await renderWorkbench();
      await user.click(view(/LLM suggestions/));
      expect(screen.getByText("suggestions shown")).toHaveTextContent("2 suggestions shown");

      await user.click(screen.getByRole("button", { name: label }));
      await waitFor(() => expect(posted(api, "workbench/suggestions")).toEqual([{ term_ids: ["T4", "T2"], accept }]));
    });

    it("offers no bulk answer outside the suggestions view or when it is empty", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ terms: [QELMAR] }) });
      const { user } = await renderWorkbench();
      await user.click(view(/LLM suggestions/));
      expect(screen.queryByRole("button", { name: "Accept all shown" })).not.toBeInTheDocument();
      await user.click(view(/All terms/));
      expect(screen.queryByRole("button", { name: "Accept all shown" })).not.toBeInTheDocument();
    });

    it("shows why an answer was refused", async () => {
      workbenchApi({ [`POST ${BASE}/workbench/suggestions`]: apiError("the candidate was rebuilt; reload", 409) });
      const { user, onChanged } = await renderWorkbench();
      await user.click(view(/To publish/));
      await user.click(within(card("Anchor")).getByRole("button", { name: "Accept" }));
      expect(await screen.findByRole("status")).toHaveTextContent("the candidate was rebuilt; reload");
      expect(onChanged).not.toHaveBeenCalled();
    });
  });

  describe("LLM runs", () => {
    it("counts the terms each task would send and starts the one clicked", async () => {
      const api = workbenchApi();
      const { user, onChanged } = await renderWorkbench();
      expect(screen.getByRole("button", { name: "Resolve conflicts (1)" })).toHaveAttribute("title", "Pick one existing book translation for each pending conflict");
      expect(screen.getByRole("button", { name: "Flag generic terms (2)" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Suggest promotions (1)" })).toBeEnabled();

      await user.click(screen.getByRole("button", { name: "Flag generic terms (2)" }));
      expect(await screen.findByRole("status")).toHaveTextContent("LLM suggestions started; they appear here when the run finishes.");
      expect(posted(api, "suggest")).toEqual([{ task: "generic" }]);
      expect(onChanged).toHaveBeenCalledOnce();
    });

    it("disables a task with nothing to send", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ terms: [SWORD] }) });
      await renderWorkbench();
      for (const name of ["Resolve conflicts (0)", "Flag generic terms (0)", "Suggest promotions (0)"]) {
        expect(screen.getByRole("button", { name })).toBeDisabled();
      }
    });

    it("shows why a run could not start", async () => {
      workbenchApi({ [`POST ${BASE}/suggest`]: apiError("Ollama is not reachable", 502) });
      const { user, onChanged } = await renderWorkbench();
      await user.click(screen.getByRole("button", { name: "Resolve conflicts (1)" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Ollama is not reachable");
      expect(onChanged).not.toHaveBeenCalled();
      expect(screen.getByRole("button", { name: "Resolve conflicts (1)" })).toBeEnabled();
    });

    it("locks the tasks and opens the log while a run is in progress", async () => {
      const api = workbenchApi({ [`GET ${BASE}/log`]: { file: "suggest-0001.log", lines: ["[llm] T2 resolved", "streaming — 40 tokens", "T9 failed: timeout"] } });
      await renderWorkbench({ label: "LLM suggestions", running: true, outcome: "running", output_tail: "" });

      expect(screen.getByText("LLM suggestions is running…")).toBeInTheDocument();
      for (const name of [/Resolve conflicts/, /Flag generic terms/, /Suggest promotions/]) expect(screen.getByRole("button", { name })).toBeDisabled();
      expect(await screen.findByText("suggest-0001.log")).toBeInTheDocument();
      expect(screen.getByText("[llm] T2 resolved")).toBeInTheDocument();
      expect(screen.getByText("T9 failed: timeout")).toBeInTheDocument();
      expect(screen.queryByText(/streaming —/)).not.toBeInTheDocument(); // heartbeats are noise
      expect(screen.getByRole("button", { name: "Hide LLM run log" })).toBeInTheDocument();
      expect(api.requested(`${BASE}/log`)).toBe(true);
    });

    it("refreshes the log every two seconds while the run is in progress", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let lines = ["first line"];
      workbenchApi({ [`GET ${BASE}/log`]: () => ({ file: "suggest-0001.log", lines }) });
      await renderWorkbench({ label: "LLM suggestions", running: true, outcome: "running", output_tail: "" });
      expect(await screen.findByText("first line")).toBeInTheDocument();

      lines = ["first line", "second line"];
      await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
      expect(await screen.findByText("second line")).toBeInTheDocument();
    });

    it("shows the output of a run that failed", async () => {
      workbenchApi();
      await renderWorkbench({ label: "LLM suggestions", running: false, outcome: "failed", output_tail: "RuntimeError: model not found" });
      expect(screen.getByText(/run failed:/)).toHaveTextContent("The LLM suggestions run failed:RuntimeError: model not found");
      expect(screen.getByRole("button", { name: "Resolve conflicts (1)" })).toBeEnabled();
      expect(screen.queryByText(/is running…/)).not.toBeInTheDocument();
    });

    it("says nothing about a run that finished cleanly", async () => {
      workbenchApi();
      await renderWorkbench({ label: "LLM suggestions", running: false, outcome: "completed", output_tail: "" });
      expect(screen.queryByText(/run failed:|is running…/)).not.toBeInTheDocument();
    });

    it("shows and hides the last run's log on request", async () => {
      const api = workbenchApi({ [`GET ${BASE}/log`]: { file: "suggest-0001.log", lines: ["streaming — 40 tokens"] } });
      const { user } = await renderWorkbench();
      expect(api.requested(`${BASE}/log`)).toBe(false);

      await user.click(screen.getByRole("button", { name: "Show LLM run log" }));
      expect(await screen.findByText("suggest-0001.log")).toBeInTheDocument();
      expect(screen.getByText("No output yet.")).toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Hide LLM run log" }));
      expect(screen.queryByText("suggest-0001.log")).not.toBeInTheDocument();
    });

    it("says when there has been no run", async () => {
      workbenchApi();
      const { user } = await renderWorkbench();
      await user.click(screen.getByRole("button", { name: "Show LLM run log" }));
      expect(await screen.findByText("No LLM run yet.")).toBeInTheDocument();
    });
  });

  describe("publishing", () => {
    const publishButton = () => screen.getByRole("button", { name: "Publish v002" });

    it("says what the new version contains before publishing it", async () => {
      const api = workbenchApi({
        [`GET ${BASE}/workbench`]: workbench({ publish: { keep: 3, pending: 1, added: ["Qelmar", "Anchor"], changed: ["Sunblade"], removed: ["Oldterm"], stale: false } }),
      });
      const { user, onPublished } = await renderWorkbench();
      await user.click(publishButton());

      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Publish v002?");
      expect(dialog).toHaveTextContent("3 kept terms become series glossary v002. It is never changed afterwards.");
      expect(dialog).toHaveTextContent("2 added: Qelmar, Anchor");
      expect(dialog).toHaveTextContent("1 changed from v001: Sunblade");
      expect(dialog).toHaveTextContent("1 removed from v001: Oldterm");
      expect(dialog).toHaveTextContent("1 pending term is left out and stays in its book glossary.");
      expect(within(dialog).getByRole("button", { name: "Publish v002" })).toHaveFocus();

      await user.click(within(dialog).getByRole("button", { name: "Publish v002" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Published v002.");
      expect(posted(api, "publish")).toEqual([{}]);
      expect(onPublished).toHaveBeenCalledOnce();
    });

    it("publishes nothing when cancelled", async () => {
      const api = workbenchApi();
      const { user, onPublished } = await renderWorkbench();
      await user.click(publishButton());
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(posted(api, "publish")).toEqual([]);
      expect(onPublished).not.toHaveBeenCalled();
    });

    it("lists at most twelve names per change and leaves out empty lists and the pending warning", async () => {
      const added = Array.from({ length: 14 }, (_, i) => `Term${i + 1}`);
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ publish: { keep: 14, pending: 0, added, changed: [], removed: [], stale: false } }) });
      const { user } = await renderWorkbench();
      await user.click(publishButton());

      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent(`14 added: ${added.slice(0, 12).join(", ")}, …`);
      expect(dialog).not.toHaveTextContent("Term13");
      expect(dialog).not.toHaveTextContent(/changed from|removed from|left out/);
    });

    it("counts several pending terms in the warning", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ publish: { keep: 3, pending: 4, added: [], changed: [], removed: [], stale: false } }) });
      const { user } = await renderWorkbench();
      await user.click(publishButton());
      expect(await screen.findByRole("alertdialog")).toHaveTextContent("4 pending terms are left out and stay in their book glossaries.");
    });

    it("cannot publish a candidate that predates the latest version, and says what to do", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ publish: { keep: 3, pending: 0, added: [], changed: [], removed: [], stale: true } }) });
      await renderWorkbench();
      expect(publishButton()).toBeDisabled();
      expect(publishButton()).toHaveAttribute("title", "A newer version was published; rebuild the candidate on the Books tab");
      expect(screen.getByText("This candidate predates v001; rebuild it on the Books tab before publishing.")).toBeInTheDocument();
    });

    it("cannot publish a candidate with no kept terms", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ publish: { keep: 0, pending: 5, added: [], changed: [], removed: [], stale: false } }) });
      await renderWorkbench();
      expect(publishButton()).toBeDisabled();
    });

    it("shows why publishing failed", async () => {
      workbenchApi({ [`POST ${BASE}/publish`]: apiError("v002 already exists", 409) });
      const { user, onPublished } = await renderWorkbench();
      await user.click(publishButton());
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Publish v002" }));
      expect(await screen.findByRole("status")).toHaveTextContent("v002 already exists");
      expect(onPublished).not.toHaveBeenCalled();
      expect(publishButton()).toBeEnabled();
    });
  });

  describe("long lists", () => {
    const many = Array.from({ length: 170 }, (_, i) => term(`M${i}`, `Term ${String(i).padStart(3, "0")}`, "译", { decision: "pending" }));

    it("shows 150 terms at a time and more on request", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ terms: many }) });
      const { user } = await renderWorkbench();
      expect(shownTerms()).toHaveLength(150);

      await user.click(screen.getByRole("button", { name: "Show 20 more of 20" }));
      expect(shownTerms()).toHaveLength(170);
      expect(screen.queryByRole("button", { name: /^Show \d+ more/ })).not.toBeInTheDocument();
    });

    it("goes back to the first 150 when the view or the search changes", async () => {
      workbenchApi({ [`GET ${BASE}/workbench`]: workbench({ terms: many }) });
      const { user } = await renderWorkbench();
      await user.click(screen.getByRole("button", { name: "Show 20 more of 20" }));
      await user.click(view(/All terms/));
      expect(shownTerms()).toHaveLength(150);

      await user.type(screen.getByPlaceholderText("Search terms"), "Term 16");
      expect(shownTerms()).toEqual(["Term 160", "Term 161", "Term 162", "Term 163", "Term 164", "Term 165", "Term 166", "Term 167", "Term 168", "Term 169"]);
    });
  });
});

// -- journeys -------------------------------------------------------------------
// README "Build a shared series glossary": terms agreed by two books are kept,
// disagreements wait for a decision, single-book terms stay in their book; LLM
// suggestions change nothing until accepted; publishing freezes the kept terms.

describe("Series workbench: an editor settles the names across the Holmes novels", () => {
  const BOOKS = [
    { job_id: "a-study-in-scarlet", volume: 1, title: "A Study in Scarlet" },
    { job_id: "the-sign-of-the-four", volume: 2, title: "The Sign of the Four" },
  ];
  const both = (one: string, two = one) => ({ "a-study-in-scarlet": [one], "the-sign-of-the-four": [two] });
  const HOLMES = term("H1", "Sherlock Holmes", "夏洛克·福尔摩斯", { books: both("夏洛克·福尔摩斯") });
  const WATSON = term("H2", "Watson", "华生", {
    origin: "conflict", decision: "pending", books: both("华生", "沃森"), reason: "The two books translate this name differently.",
  });
  const LESTRADE = term("H3", "Lestrade", "雷斯垂德", { origin: "conflict", decision: "pending", books: both("雷斯垂德", "莱斯特雷德") });
  const CAB = term("H4", "cab", "马车", { category: "item", books: both("马车") });
  const TONGA = term("H5", "Tonga", "童格", {
    origin: "single_book", decision: "drop", books: { "the-sign-of-the-four": ["童格"] }, mentions: { "the-sign-of-the-four": 23 },
  });
  const NAMES = [HOLMES, WATSON, LESTRADE, CAB, TONGA];

  /** The candidate on a pretend server that applies each decision the way the real one does. */
  function candidate() {
    const server = { terms: NAMES as Term[], published: false };
    const view = () => {
      const kept = server.terms.filter((t) => t.decision === "keep");
      return workbench({
        terms: server.terms, books: BOOKS.map((b) => b.job_id), book_info: BOOKS, based_on: null, latest: null, next_version: "v001",
        publish: { keep: kept.length, pending: server.terms.filter((t) => t.decision === "pending").length, added: kept.map((t) => t.source), changed: [], removed: [], stale: false },
      });
    };
    const api = workbenchApi({
      [`GET ${BASE}/workbench`]: view,
      [`POST ${BASE}/workbench/decide`]: (body: { term_ids: string[]; decision: Term["decision"]; reason: string; target: string | null }) => {
        server.terms = server.terms.map((t) => (body.term_ids.includes(t.term_id)
          ? { ...t, decision: body.decision, decided_by: "user", reason: body.reason, target: body.target ?? t.target }
          : t));
        return view();
      },
      [`POST ${BASE}/workbench/suggestions`]: (body: { term_ids: string[]; accept: boolean }) => {
        server.terms = server.terms.map((t) => (body.term_ids.includes(t.term_id)
          ? { ...t, suggestion: null, ...(body.accept ? { decision: "drop" as const, decided_by: "llm-accepted" as const } : {}) }
          : t));
        return view();
      },
      [`POST ${BASE}/publish`]: () => { server.published = true; return { version: "v001" }; },
      [`GET ${BASE}/term`]: {
        source: "Watson",
        books: [
          { ...BOOKS[0], mentions: 412, snippets: [], glossary: [{ target: "华生", category: "person", note: "", evidence: ["“Dr. Watson, Mr. Sherlock Holmes,” said Stamford, introducing us."] }] },
          { ...BOOKS[1], mentions: 287, snippets: [], glossary: [{ target: "沃森", category: "person", note: "", evidence: ["“My practice has extended recently to the Continent,” said Holmes to Watson."] }] },
        ],
      },
    });
    return { api, server };
  }

  it("finds Watson spelled two ways, reads how each book uses the name, picks one for the series, and says why", async () => {
    const { api } = candidate();
    const { user, onChanged } = await renderWorkbench();
    expect(shownTerms()).toEqual(["Watson", "Lestrade"]); // the two names the books disagree on
    const watson = within(card("Watson"));
    expect(watson.getByTitle("A Study in Scarlet (a-study-in-scarlet)")).toHaveTextContent("Vol. 1 华生");
    expect(watson.getByTitle("The Sign of the Four (the-sign-of-the-four)")).toHaveTextContent("Vol. 2 沃森");

    await user.click(watson.getByRole("button", { name: "Evidence" }));
    expect(await watson.findByText(/412 mentions in the text/)).toBeInTheDocument();
    expect(watson.getByText(/introducing us/)).toHaveTextContent("“Dr. Watson, Mr. Sherlock Holmes,” said Stamford, introducing us.");

    // The first novel's rendering is the familiar one: keep it for every book.
    await user.click(watson.getByRole("button", { name: "Decide" }));
    await user.click(watson.getByRole("button", { name: "华生" }));
    await user.click(watson.getByRole("radio", { name: "Canonical form for the series." }));
    await user.click(watson.getByRole("button", { name: "Keep" }));

    expect(await screen.findByRole("status")).toHaveTextContent("Saved: 1 term kept.");
    expect(api.posted(`${BASE}/workbench/decide`)).toEqual([
      { term_ids: ["H2"], decision: "keep", reason: "Canonical form for the series.", target: null, category: null, unlock: false },
    ]);
    // One name fewer to decide; Watson is now among the terms to publish.
    expect(view(/Needs a decision/)).toHaveTextContent("Needs a decision1");
    expect(shownTerms()).toEqual(["Lestrade"]);
    await user.click(view(/To publish/));
    expect(within(card("Watson")).getByText("keep · by you")).toBeInTheDocument();
    expect(within(card("Watson")).getByText("Canonical form for the series.")).toBeInTheDocument();
    expect(onChanged).toHaveBeenCalled();
  });

  it("settles Lestrade with the second book's spelling and a reason of their own", async () => {
    const { api } = candidate();
    const { user } = await renderWorkbench();
    const lestrade = within(card("Lestrade"));
    await user.click(lestrade.getByRole("button", { name: "Decide" }));
    await user.click(lestrade.getByRole("button", { name: "莱斯特雷德" }));
    await user.type(lestrade.getByPlaceholderText("Describe why (at least 3 characters)"), "Closer to the English pronunciation.");
    await user.click(lestrade.getByRole("button", { name: "Keep with these changes" }));

    await waitFor(() => expect(api.posted(`${BASE}/workbench/decide`)).toEqual([
      { term_ids: ["H3"], decision: "keep", reason: "Closer to the English pronunciation.", target: "莱斯特雷德", category: null, unlock: false },
    ]));
    await user.click(view(/To publish/));
    expect(within(card("Lestrade")).getByText("莱斯特雷德", { selector: ".term-zh" })).toBeInTheDocument();
  });

  it("asks the LLM which entries are ordinary words, agrees about “cab”, and keeps the detective", async () => {
    const { api, server } = candidate();
    const { user } = await renderWorkbench();
    await user.click(screen.getByRole("button", { name: /Flag generic terms/ }));
    expect(await screen.findByRole("status")).toHaveTextContent("LLM suggestions started; they appear here when the run finishes.");
    expect(api.posted(`${BASE}/suggest`)).toEqual([{ task: "generic" }]);

    // The run has finished: its suggestions are waiting, and nothing has changed yet.
    cleanup();
    const flag = (t: Term, rationale: string) => ({ ...t, suggestion: { kind: "drop_generic" as const, target: null, rationale, model: "qwen3:14b" } });
    server.terms = server.terms.map((t) => (t === CAB ? flag(t, "A common noun.") : t === HOLMES ? flag(t, "Appears often.") : t));
    const second = await renderWorkbench();
    await second.user.click(view(/LLM suggestions/));
    expect(shownTerms()).toEqual(["Sherlock Holmes", "cab"]);
    expect(within(card("cab")).getByText("keep")).toBeInTheDocument();

    await second.user.click(within(card("cab")).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(shownTerms()).toEqual(["Sherlock Holmes"]));
    await second.user.click(within(card("Sherlock Holmes")).getByRole("button", { name: "Reject" }));
    await waitFor(() => expect(screen.getByText("No terms in this view.")).toBeInTheDocument());

    expect(api.posted(`${BASE}/workbench/suggestions`)).toEqual([{ term_ids: ["H4"], accept: true }, { term_ids: ["H1"], accept: false }]);
    await second.user.click(view(/Dropped/));
    expect(shownTerms()).toEqual(["cab", "Tonga"]);
    await second.user.click(view(/To publish/));
    expect(shownTerms()).toEqual(["Sherlock Holmes"]);
  });

  it("decides the last open names in one go and publishes the first version of the series glossary", async () => {
    const { api, server } = candidate();
    const { user, onPublished } = await renderWorkbench();

    await user.click(screen.getByRole("button", { name: "Publish v001" }));
    expect(await screen.findByRole("alertdialog")).toHaveTextContent("2 pending terms are left out and stay in their book glossaries.");
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Cancel" })); // not yet: settle them first
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());

    await user.click(screen.getByRole("checkbox", { name: "Select Watson" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Lestrade" }));
    const bulk = within(screen.getByText("selected").closest("section") as HTMLElement);
    await user.click(bulk.getByRole("radio", { name: "The books agree after review." }));
    await user.click(bulk.getByRole("button", { name: "Keep" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Saved: 2 terms kept.");
    expect(screen.getByText(/candidate terms/)).toHaveTextContent("5 candidate terms · 4 to publish");

    await user.click(screen.getByRole("button", { name: "Publish v001" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("4 kept terms become series glossary v001. It is never changed afterwards.");
    expect(dialog).toHaveTextContent("4 added: Sherlock Holmes, Watson, Lestrade, cab");
    expect(dialog).not.toHaveTextContent("left out");
    await user.click(within(dialog).getByRole("button", { name: "Publish v001" }));

    expect(await screen.findByText("Published v001.")).toBeInTheDocument();
    expect(server.published).toBe(true);
    expect(api.posted(`${BASE}/publish`)).toEqual([{}]);
    expect(onPublished).toHaveBeenCalledOnce();
  });
});
