import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { apiError, deferred, mockApi } from "../test/mockApi";

// -- fixtures -----------------------------------------------------------------

const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();

const LANGUAGES = {
  languages: [
    { code: "en", name: "English", tier: "tuned" },
    { code: "zh", name: "Simplified Chinese", tier: "tuned" },
    { code: "ja", name: "Japanese", tier: "generic" },
  ],
  support: null,
  error: "",
};

const seriesList = (overrides: Record<string, unknown> = {}) => ({
  series: [
    { series_id: "qel", name: "The Qel Cycle", direction: "en-zh", books: 2, latest: "v001", pending: 3 },
    { series_id: "new one", name: "Fresh Start", direction: "en>ja", books: 0, latest: null, pending: null },
    { series_id: "done", name: "Decided", direction: "zh-en", books: 4, latest: "v003", pending: 0 },
  ],
  directions: ["en-zh", "zh-en"],
  ...overrides,
});

const BOOKS = [
  { job_id: "b1", volume: 1, added_at: ago(9000), glossary: "approved", version: "v001" },
  { job_id: "b2", volume: 2, added_at: ago(8000), glossary: "resolved", version: null },
  { job_id: "b 3", volume: 3, added_at: ago(7000), glossary: "draft", version: null },
  { job_id: "b4", volume: null, added_at: ago(6000), glossary: "not ready", version: null },
];

const VERSIONS = [
  { version: "v001", created_at: ago(3 * 86400), source_jobs: ["b1"], term_count: 12, based_on: null },
  { version: "v002", created_at: ago(7200), source_jobs: [], term_count: 15, based_on: "v001" },
];

const WORKBENCH = { based_on: "v002", built_at: ago(10), terms: 20, pending: 3, keep: 15 };

const detail = (overrides: Record<string, unknown> = {}) => ({
  series_id: "qel",
  name: "The Qel Cycle",
  direction: "en-zh",
  books: BOOKS,
  versions: VERSIONS,
  workbench: null,
  addable: [
    { job_id: "loose-1", source: "one.epub", glossary: "approved" },
    { job_id: "loose-2", source: "two.epub", glossary: "resolved" },
  ],
  process: null,
  ...overrides,
});

const workbenchView = {
  series_id: "qel", based_on: "v002", built_at: ago(10), not_ready: [], books: ["b1"], next_version: "v003", latest: "v002",
  terms: [{
    term_id: "T1", source: "Qelmar", target: "凯尔玛", category: "person", origin: "consensus", books: { b1: ["凯尔玛"] },
    decision: "keep", decided_by: "rule", reason: "", locked_from: null, suggestion: null,
  }],
  publish: { keep: 1, pending: 0, added: ["Qelmar"], changed: [], removed: [], stale: false },
};

const BASE = "/api/series/qel";

function seriesApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/series": seriesList(),
    "POST /api/series": { ok: true },
    "GET /api/languages": LANGUAGES,
    [`GET ${BASE}/detail`]: detail(),
    [`POST ${BASE}/books`]: detail(),
    [`POST ${BASE}/books/remove`]: detail(),
    [`POST ${BASE}/build`]: detail({ workbench: WORKBENCH }),
    [`GET ${BASE}/workbench`]: workbenchView,
    [`POST ${BASE}/publish`]: { version: "v003" },
    ...overrides,
  });
}

function renderAt(path: string) {
  window.history.pushState({}, "", path);
  const user = userEvent.setup();
  render(<App />);
  return user;
}

const rowOf = (text: string) => screen.getByText(text).closest("tr") as HTMLElement;
const tab = (name: RegExp | string) => screen.getByRole("tab", { name });

afterEach(() => vi.useRealTimers());

// -- tests --------------------------------------------------------------------

describe("Series list", () => {
  it("lists each series with its direction, books, latest version, and workbench state", async () => {
    seriesApi();
    renderAt("/series");
    expect(screen.getByText("Loading…")).toBeInTheDocument();

    const qel = within((await screen.findByRole("link", { name: "The Qel Cycle" })).closest("tr") as HTMLElement);
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    expect(qel.getByRole("link", { name: "The Qel Cycle" })).toHaveAttribute("href", "/series/qel");
    expect(qel.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["The Qel Cycle qel", "EN → ZH", "2", "v001", "3 pending"]);

    const fresh = within(rowOf("Fresh Start"));
    expect(fresh.getByRole("link", { name: "Fresh Start" })).toHaveAttribute("href", "/series/new%20one");
    expect(fresh.getAllByRole("cell").map((cell) => cell.textContent)).toEqual(["Fresh Start new one", "EN → JA", "0", "none published", "—"]);

    expect(within(rowOf("Decided")).getByText("ready to publish")).toBeInTheDocument();
  });

  it("says when there are no series yet", async () => {
    seriesApi({ "GET /api/series": seriesList({ series: [] }) });
    renderAt("/series");
    expect(await screen.findByText("No series yet.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ New series" })).toBeEnabled();
  });

  it("reports a list that cannot be loaded and offers no new series", async () => {
    seriesApi({ "GET /api/series": apiError("runs folder is not readable", 500) });
    renderAt("/series");
    expect(await screen.findByText("runs folder is not readable")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "+ New series" })).toBeDisabled();
  });

  describe("new series dialog", () => {
    const open = async (user: ReturnType<typeof userEvent.setup>) => {
      await screen.findByRole("link", { name: "The Qel Cycle" });
      await user.click(screen.getByRole("button", { name: "+ New series" }));
      return within(screen.getByRole("dialog", { name: "New series" }));
    };

    it("suggests the series id from the name and creates the series", async () => {
      const api = seriesApi({ "GET /api/series/the-ember-saga/detail": detail({ series_id: "the-ember-saga", name: "The Ember Saga" }) });
      const user = renderAt("/series");
      const dialog = await open(user);
      const name = dialog.getByLabelText("Name");
      expect(name).toHaveFocus();
      expect(dialog.getByRole("button", { name: "Create series" })).toBeDisabled(); // no id yet
      expect(dialog.getByText(/Stored under/)).toHaveTextContent("runs/.series/…");

      await user.type(name, "The Ember Saga");
      expect(dialog.getByLabelText("Series ID")).toHaveValue("the-ember-saga");
      expect(dialog.getByText(/Stored under/)).toHaveTextContent("runs/.series/the-ember-saga");
      expect(dialog.getByRole("combobox", { name: "From language" })).toHaveValue("en");
      expect(dialog.getByRole("combobox", { name: "Into language" })).toHaveValue("zh");

      await user.click(dialog.getByRole("button", { name: "Create series" }));
      await waitFor(() => expect(window.location.pathname).toBe("/series/the-ember-saga"));
      expect(api.posted("/api/series")).toEqual([{ series_id: "the-ember-saga", name: "The Ember Saga", direction: "en-zh" }]);
    });

    it("keeps an id the user typed when the name changes", async () => {
      const api = seriesApi({ "GET /api/series/ember/detail": detail({ series_id: "ember" }) });
      const user = renderAt("/series");
      const dialog = await open(user);
      await user.type(dialog.getByLabelText("Name"), "The Ember");
      const id = dialog.getByLabelText("Series ID");
      await user.clear(id);
      await user.type(id, "ember");
      await user.type(dialog.getByLabelText("Name"), " Saga");
      expect(id).toHaveValue("ember");

      await user.click(dialog.getByRole("button", { name: "Create series" }));
      await waitFor(() => expect(api.posted("/api/series")).toEqual([{ series_id: "ember", name: "The Ember Saga", direction: "en-zh" }]));
    });

    it("will not create a series that has an id but no name", async () => {
      seriesApi();
      const user = renderAt("/series");
      const dialog = await open(user);
      await user.type(dialog.getByLabelText("Series ID"), "ember");
      expect(dialog.getByRole("button", { name: "Create series" })).toBeDisabled();
      await user.type(dialog.getByLabelText("Name"), "   ");
      expect(dialog.getByRole("button", { name: "Create series" })).toBeDisabled();
      await user.type(dialog.getByLabelText("Name"), "Ember");
      expect(dialog.getByRole("button", { name: "Create series" })).toBeEnabled();
    });

    it.each(["has space", "-leading-dash", "x".repeat(65), "sláinte"])("will not create a series with the id %j", async (value) => {
      seriesApi();
      const user = renderAt("/series");
      const dialog = await open(user);
      await user.type(dialog.getByLabelText("Name"), "Ember");
      await user.clear(dialog.getByLabelText("Series ID"));
      await user.type(dialog.getByLabelText("Series ID"), value);
      expect(dialog.getByRole("button", { name: "Create series" })).toBeDisabled();
    });

    it("creates the series for the languages picked", async () => {
      const api = seriesApi({ "GET /api/series/ember/detail": detail({ series_id: "ember" }) });
      const user = renderAt("/series");
      const dialog = await open(user);
      await user.type(dialog.getByLabelText("Name"), "Ember");
      const into = dialog.getByRole("combobox", { name: "Into language" });
      await user.clear(into);
      await user.type(into, "ja{Enter}");

      await user.click(dialog.getByRole("button", { name: "Create series" }));
      await waitFor(() => expect(api.posted("/api/series")).toEqual([{ series_id: "ember", name: "Ember", direction: "en>ja" }]));
    });

    it("shows why the series could not be created and stays open", async () => {
      seriesApi({ "POST /api/series": apiError("series qel already exists", 409) });
      const user = renderAt("/series");
      const dialog = await open(user);
      await user.type(dialog.getByLabelText("Name"), "Qel");
      await user.click(dialog.getByRole("button", { name: "Create series" }));

      expect(await screen.findByRole("status")).toHaveTextContent("series qel already exists");
      expect(screen.getByRole("dialog", { name: "New series" })).toBeInTheDocument();
      expect(dialog.getByRole("button", { name: "Create series" })).toBeEnabled();
      expect(window.location.pathname).toBe("/series");
    });

    it.each([
      ["Cancel", (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("button", { name: "Cancel" }))],
      ["Escape", (user: ReturnType<typeof userEvent.setup>) => user.keyboard("{Escape}")],
      ["a click on the backdrop", (user: ReturnType<typeof userEvent.setup>) => user.click(screen.getByRole("dialog").parentElement as HTMLElement)],
    ])("closes on %s without creating anything", async (_how, close) => {
      const api = seriesApi();
      const user = renderAt("/series");
      await open(user);
      await close(user);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
      expect(api.posted("/api/series")).toEqual([]);
    });
  });
});

describe("Series page", () => {
  it("shows the series, its direction, and its latest version", async () => {
    const gate = deferred();
    seriesApi({ [`GET ${BASE}/detail`]: () => gate.promise });
    renderAt("/series/qel");
    expect(screen.getByText("Loading…")).toBeInTheDocument();
    expect(within(screen.getByRole("banner")).getByText("qel")).toBeInTheDocument(); // the id until the name is known

    gate.resolve(detail());
    const head = (await screen.findByText("The Qel Cycle", { selector: ".title" })).parentElement as HTMLElement;
    expect(head).toHaveTextContent("The Qel Cycle" + "qel" + "EN → ZH" + "v002");
    expect(within(screen.getByRole("banner")).getByText("The Qel Cycle")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("says when nothing has been published yet", async () => {
    seriesApi({ [`GET ${BASE}/detail`]: detail({ versions: [] }) });
    renderAt("/series/qel");
    expect(await screen.findByText("no version yet")).toBeInTheDocument();
    expect(tab("Versions (0)")).toBeInTheDocument();
  });

  it("reports a series that cannot be loaded", async () => {
    seriesApi({ [`GET ${BASE}/detail`]: apiError("series qel does not exist", 404) });
    renderAt("/series/qel");
    expect(await screen.findByText("series qel does not exist")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
  });

  it("opens a series whose id has a space in it", async () => {
    const api = seriesApi({ "GET /api/series/new%20one/detail": detail({ series_id: "new one", name: "Fresh Start" }) });
    renderAt("/series/new%20one");
    expect(await screen.findByText("Fresh Start", { selector: ".title" })).toBeInTheDocument();
    expect(api.requested("/api/series/new%20one/detail")).toBe(true);
  });

  describe("books", () => {
    it("lists each book with its volume, glossary state, and pinned version", async () => {
      seriesApi();
      renderAt("/series/qel");
      expect(await screen.findByRole("heading", { name: "Books (4)" })).toBeInTheDocument();
      const row = (job: string) => within(rowOf(job)).getAllByRole("cell").map((cell) => cell.textContent);
      expect(row("b1")).toEqual(["1", "b1", "approved", "v001 · v002 available", ""]);
      expect(row("b2")).toEqual(["2", "b2", "at glossary gate", "approve with v002 →", "Remove"]);
      expect(row("b 3")).toEqual(["3", "b 3", "draft · not started", "not pinned", "Remove"]);
      expect(row("b4")).toEqual(["", "b4", "glossary not ready", "not pinned", "Remove"]);
    });

    it("links a draft book to its Config tab and any other book to its Glossary tab", async () => {
      seriesApi();
      renderAt("/series/qel");
      expect(await screen.findByRole("link", { name: "b 3" })).toHaveAttribute("href", "/jobs/b%203/config");
      expect(screen.getByRole("link", { name: "b1" })).toHaveAttribute("href", "/jobs/b1/glossary");
      expect(screen.getByRole("link", { name: "approve with v002 →" })).toHaveAttribute("href", "/jobs/b2/glossary");
    });

    it("shows an unfamiliar glossary status in the server's own words", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ books: [{ ...BOOKS[1], glossary: "extracting" }], versions: [] }) });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (1)" });
      expect(within(rowOf("b2")).getByText("extracting")).toBeInTheDocument();
      expect(within(rowOf("b2")).getByText("not pinned")).toBeInTheDocument(); // nothing to approve with yet
    });

    it("does not offer a newer version to a book pinned to the latest", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ versions: [VERSIONS[0]] }) });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      expect(within(rowOf("b1")).getAllByRole("cell")[3]).toHaveTextContent(/^v001$/);
    });

    it("removes a book that is not pinned", async () => {
      const api = seriesApi({ [`POST ${BASE}/books/remove`]: detail({ books: BOOKS.filter((b) => b.job_id !== "b2") }) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await user.click(within(rowOf("b2")).getByRole("button", { name: "Remove" }));

      expect(await screen.findByRole("status")).toHaveTextContent("b2 removed.");
      expect(api.posted(`${BASE}/books/remove`)).toEqual([{ job_id: "b2" }]);
      expect(screen.getByRole("heading", { name: "Books (3)" })).toBeInTheDocument();
      expect(screen.queryByRole("link", { name: "b2" })).not.toBeInTheDocument();
    });

    it("shows why a book could not be removed and keeps it listed", async () => {
      seriesApi({ [`POST ${BASE}/books/remove`]: apiError("b2 is running", 409) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await user.click(within(rowOf("b2")).getByRole("button", { name: "Remove" }));
      expect(await screen.findByRole("status")).toHaveTextContent("b2 is running");
      expect(screen.getByRole("link", { name: "b2" })).toBeInTheDocument();
      expect(within(rowOf("b2")).getByRole("button", { name: "Remove" })).toBeEnabled();
    });

    it("says when the series has no books", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ books: [] }) });
      renderAt("/series/qel");
      expect(await screen.findByRole("heading", { name: "Books (0)" })).toBeInTheDocument();
      expect(screen.getByText("No books yet. Add jobs below.")).toBeInTheDocument();
      expect(screen.getByText("0 of 0 books ready.")).toBeInTheDocument();
    });
  });

  describe("adding books", () => {
    const pick = (job: string) => screen.getByRole("checkbox", { name: new RegExp(job) });

    it("adds the ticked jobs as the next volumes, in the order they were ticked", async () => {
      const api = seriesApi({ [`POST ${BASE}/books`]: detail({ addable: [] }) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      expect(pick("loose-1").closest("label")).toHaveTextContent("loose-1 one.epub · approved");
      expect(screen.getByRole("button", { name: /^Add\s+as next volumes$/ })).toBeDisabled();

      await user.click(pick("loose-2"));
      expect(screen.getByRole("button", { name: "Add 1 as next volume" })).toBeEnabled();
      await user.click(pick("loose-1"));
      await user.click(screen.getByRole("button", { name: "Add 2 as next volumes" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Added 2 books.");
      expect(api.posted(`${BASE}/books`)).toEqual([{ job_ids: ["loose-2", "loose-1"] }]);
      expect(screen.getByText("No existing EN → ZH jobs outside a series to add.")).toBeInTheDocument();
    });

    it("adds a single job, and unticking takes a job back out", async () => {
      const api = seriesApi();
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await user.click(pick("loose-1"));
      await user.click(pick("loose-2"));
      await user.click(pick("loose-1"));
      await user.click(screen.getByRole("button", { name: "Add 1 as next volume" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Added 1 book.");
      expect(api.posted(`${BASE}/books`)).toEqual([{ job_ids: ["loose-2"] }]);
      await waitFor(() => expect(pick("loose-2")).not.toBeChecked()); // the selection is cleared after adding
    });

    it("shows why the jobs could not be added", async () => {
      seriesApi({ [`POST ${BASE}/books`]: apiError("loose-1 translates zh-en, not en-zh", 400) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await user.click(pick("loose-1"));
      await user.click(screen.getByRole("button", { name: "Add 1 as next volume" }));
      expect(await screen.findByRole("status")).toHaveTextContent("loose-1 translates zh-en, not en-zh");
      expect(screen.getByRole("heading", { name: "Books (4)" })).toBeInTheDocument();
      expect(pick("loose-1")).toBeChecked(); // still ticked, ready for another try
      expect(screen.getByRole("button", { name: "Add 1 as next volume" })).toBeEnabled();
    });

    it("opens the new-job dialog for a new book in this series, sharing the series config", async () => {
      const BOOK = { name: "alice.epub", path: "D:\\runs\\.uploads\\alice.epub", size: 2048, format: "epub", title: "Alice", authors: [], language: "en", has_cover: true };
      const api = seriesApi({
        "GET /api/setup": { configs: [{ name: "qel.yaml" }], config_dir: "configs", runs: "runs", template: "configs/default.yaml", jobs: [] },
        "POST /api/uploads": { path: BOOK.path },
        "GET /api/book": BOOK,
        "GET /api/jobs/suggest": { config: "alice.yaml", job_id: "alice" },
        "POST /api/jobs/new": { job_id: "qel-alice", created_config: false },
      });
      const user = renderAt("/series/qel");
      await user.click(await screen.findByRole("button", { name: "+ New book" }));

      const dialog = await screen.findByRole("dialog", { name: "New book in The Qel Cycle" });
      expect(within(dialog).getByText(/The job joins the series as its next volume\./)).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Config file name")).toHaveValue("qel.yaml");
      expect(within(dialog).getByText("qel.yaml already exists in configs; the job uses it as is (shared by the series' books).")).toBeInTheDocument();
      expect(within(dialog).getByText("Defaults to the series ID plus the book name. Used as the workspace folder name.")).toBeInTheDocument();

      await user.upload(dialog.querySelector('input[type="file"]') as HTMLInputElement, new File(["book"], "alice.epub"));
      await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("qel-alice"));
      expect(within(dialog).getByLabelText("Config file name")).toHaveValue("qel.yaml"); // the book does not rename the shared config

      await user.click(within(dialog).getByRole("button", { name: "Create job" }));
      await waitFor(() => expect(window.location.pathname).toBe("/jobs/qel-alice/config"));
      expect(api.posted("/api/jobs/new")).toEqual([{ source: BOOK.path, config: "qel.yaml", job_id: "qel-alice", series_id: "qel" }]);
    });

    it("closes the new-book dialog on Cancel", async () => {
      seriesApi({ "GET /api/setup": { configs: [], config_dir: "configs", runs: "runs", template: "", jobs: [] } });
      const user = renderAt("/series/qel");
      await user.click(await screen.findByRole("button", { name: "+ New book" }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Cancel" }));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("shows why the new-book dialog could not be opened", async () => {
      seriesApi({ "GET /api/setup": apiError("config folder is missing", 500) });
      const user = renderAt("/series/qel");
      await user.click(await screen.findByRole("button", { name: "+ New book" }));
      expect(await screen.findByRole("status")).toHaveTextContent("config folder is missing");
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });
  });

  describe("candidate glossary", () => {
    const candidate = () => within(screen.getByRole("heading", { name: "Candidate glossary" }).closest("section") as HTMLElement);

    it("builds the candidate from the books that are ready", async () => {
      const api = seriesApi();
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      expect(candidate().getByText("Not built yet.")).toBeInTheDocument();
      expect(candidate().getByText("2 of 4 books ready.")).toBeInTheDocument();
      expect(candidate().getByText(/Collects the glossaries/)).toHaveTextContent("Terms of v002 are carried and locked.");
      const build = candidate().getByRole("button", { name: "Build candidate" });
      expect(build).toHaveAttribute("title", "Deterministic, no LLM calls");
      expect(tab("Workbench")).toBeDisabled();
      expect(tab("Workbench")).toHaveAttribute("title", "Build the candidate on the Books tab first");

      await user.click(build);
      expect(await screen.findByRole("status")).toHaveTextContent("Candidate built.");
      expect(api.posted(`${BASE}/build`)).toEqual([{}]);
      expect(candidate().getByText(/^Built/)).toHaveTextContent("Built just now on top of v002: 20 terms, 15 kept, 3 pending.");
      expect(candidate().getByRole("button", { name: "Rebuild candidate" })).toBeEnabled();
      expect(tab("Workbench (3 pending)")).toBeEnabled();
    });

    it("cannot build before any book has a glossary, and says why", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ books: [BOOKS[2], BOOKS[3]], versions: [] }) });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (2)" });
      const build = candidate().getByRole("button", { name: "Build candidate" });
      expect(build).toBeDisabled();
      expect(build).toHaveAttribute("title", "No book has a resolved or approved glossary yet");
      expect(candidate().getByText(/Collects the glossaries/)).not.toHaveTextContent("carried and locked");
    });

    it("describes a first candidate with nothing pending", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ workbench: { ...WORKBENCH, based_on: null, pending: 0 } }) });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      expect(candidate().getByText(/^Built/)).toHaveTextContent("Built just now: 20 terms, 15 kept, none pending.");
      expect(tab("Workbench")).toBeEnabled();
    });

    it("shows why the build failed", async () => {
      seriesApi({ [`POST ${BASE}/build`]: apiError("b1's glossary file is corrupt", 500) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await user.click(candidate().getByRole("button", { name: "Build candidate" }));
      expect(await screen.findByRole("status")).toHaveTextContent("b1's glossary file is corrupt");
      expect(candidate().getByText("Not built yet.")).toBeInTheDocument();
    });

    it("opens the workbench from the candidate card or from its tab", async () => {
      const api = seriesApi({ [`GET ${BASE}/detail`]: detail({ workbench: WORKBENCH }) });
      const user = renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });

      await user.click(candidate().getByRole("button", { name: "Workbench" }));
      expect(await screen.findByText(/candidate terms/)).toBeInTheDocument();
      expect(api.requested(`${BASE}/workbench`)).toBe(true);
      expect(screen.queryByRole("heading", { name: "Books (4)" })).not.toBeInTheDocument();

      expect(tab("Workbench (3 pending)")).toHaveAttribute("aria-selected", "true");
      expect(tab("Books")).toHaveAttribute("aria-selected", "false");
      await user.click(tab("Books"));
      expect(tab("Books")).toHaveAttribute("aria-selected", "true");
      expect(screen.getByRole("heading", { name: "Books (4)" })).toBeInTheDocument();
      await user.click(tab("Workbench (3 pending)"));
      expect(await screen.findByText(/candidate terms/)).toBeInTheDocument();
    });

    it("shows the new version on the Versions tab as soon as it is published", async () => {
      let published = false;
      const v003 = { version: "v003", created_at: ago(1), source_jobs: ["b1"], term_count: 1, based_on: "v002" };
      const api = seriesApi({
        [`GET ${BASE}/detail`]: () => detail({ workbench: published ? null : WORKBENCH, versions: published ? [...VERSIONS, v003] : VERSIONS }),
        [`POST ${BASE}/publish`]: () => { published = true; return { version: "v003" }; },
      });
      const user = renderAt("/series/qel");
      await user.click(await screen.findByRole("tab", { name: "Workbench (3 pending)" }));
      await user.click(await screen.findByRole("button", { name: "Publish v003" }));
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Publish v003" }));

      expect(await screen.findByRole("heading", { name: "Versions" })).toBeInTheDocument();
      expect(await screen.findByRole("tab", { name: "Versions (3)" })).toHaveAttribute("aria-selected", "true");
      expect(api.posted(`${BASE}/publish`)).toEqual([{}]);
    });
  });

  describe("versions", () => {
    const openVersions = async () => {
      const user = renderAt("/series/qel");
      await user.click(await screen.findByRole("tab", { name: /^Versions/ }));
      return user;
    };
    const glossary = {
      glossary: { entries: [{ source: "Qelmar", target: "凯尔玛", category: "person" }, { source: "Arden", target: "阿登", category: "place" }] },
      glossary_pair: { pair: "en-zh", source: "English", target: "Simplified Chinese" },
    };

    it("lists the versions newest first, with their sources and the books pinned to them", async () => {
      seriesApi();
      await openVersions();
      expect(screen.getByText("Published versions are never changed. Each book keeps the version it is pinned to.")).toBeInTheDocument();
      const rows = screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell").map((cell) => cell.textContent));
      expect(rows).toEqual([
        ["v002 ← v001", "2 h ago", "15", "imported", "—", "Terms"],
        ["v001", "3 d ago", "12", "b1", "b1", "Terms"],
      ]);
    });

    it("says when nothing has been published", async () => {
      seriesApi({ [`GET ${BASE}/detail`]: detail({ versions: [] }) });
      await openVersions();
      expect(screen.getByText("Nothing published yet.")).toBeInTheDocument();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });

    it("shows a version's terms on request and hides them again", async () => {
      const api = seriesApi({ [`GET ${BASE}/version`]: glossary });
      const user = await openVersions();
      await user.click(within(rowOf("v001")).getByRole("button", { name: "Terms" }));

      expect(await screen.findByRole("heading", { name: "v001 terms (2)" })).toBeInTheDocument();
      expect(api.requested(`${BASE}/version?v=v001`)).toBe(true);
      const table = screen.getAllByRole("table")[1];
      expect(within(table).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["English", "Simplified Chinese", "Category"]);
      expect(within(table).getByText("Qelmar")).toHaveAttribute("lang", "en");
      expect(within(table).getByText("凯尔玛")).toHaveAttribute("lang", "zh-CN");
      expect(within(table).getByText("place")).toBeInTheDocument();

      await user.click(within(rowOf("v001")).getByRole("button", { name: "Hide" }));
      expect(screen.queryByRole("heading", { name: /terms \(/ })).not.toBeInTheDocument();
    });

    it("switches to another version's terms", async () => {
      seriesApi({
        [`GET ${BASE}/version`]: (_: unknown, url: URL) =>
          url.searchParams.get("v") === "v002" ? { glossary: { entries: [glossary.glossary.entries[0]] } } : glossary,
      });
      const user = await openVersions();
      await user.click(within(rowOf("v001")).getByRole("button", { name: "Terms" }));
      await screen.findByRole("heading", { name: "v001 terms (2)" });

      const newest = within(screen.getAllByRole("table")[0]).getAllByRole("row")[1];
      await user.click(within(newest).getByRole("button", { name: "Terms" }));
      expect(await screen.findByRole("heading", { name: "v002 terms (1)" })).toBeInTheDocument();
      expect(within(rowOf("v001")).getByRole("button", { name: "Terms" })).toBeInTheDocument();
      // An older server sends no pair: the headers fall back to English and Chinese.
      expect(within(screen.getAllByRole("table")[1]).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["English", "Chinese", "Category"]);
    });

    it("shows why a version's terms could not be loaded", async () => {
      seriesApi({ [`GET ${BASE}/version`]: apiError("v001 is missing on disk", 500) });
      const user = await openVersions();
      await user.click(within(rowOf("v001")).getByRole("button", { name: "Terms" }));
      expect(await screen.findByRole("status")).toHaveTextContent("v001 is missing on disk");
      expect(screen.queryByRole("heading", { name: /terms \(/ })).not.toBeInTheDocument();
    });
  });

  describe("background LLM run", () => {
    const DETAIL = `${BASE}/detail`;

    it("checks on a running LLM run every three seconds, and stops when it ends", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let running = true;
      const api = seriesApi({
        [`GET ${DETAIL}`]: () => detail({ process: { label: "LLM suggestions", running, outcome: running ? "running" : "completed", output_tail: "" } }),
      });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      expect(api.count(DETAIL)).toBe(1);

      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(api.count(DETAIL)).toBe(2);

      running = false;
      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(api.count(DETAIL)).toBe(3);
      await act(async () => { await vi.advanceTimersByTimeAsync(9000); });
      expect(api.count(DETAIL)).toBe(3);
    });

    it("drops the error of a failed refresh as soon as the next one works", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let requests = 0;
      seriesApi({
        [`GET ${DETAIL}`]: () => {
          requests += 1;
          return requests === 2
            ? apiError("server restarting", 503)
            : detail({ process: { label: "LLM suggestions", running: true, outcome: "running", output_tail: "" } });
        },
      });
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });

      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      expect(await screen.findByText("server restarting")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Books (4)" })).toBeInTheDocument(); // the page stays

      await act(async () => { await vi.advanceTimersByTimeAsync(3000); });
      await waitFor(() => expect(screen.queryByText("server restarting")).not.toBeInTheDocument());
    });

    it("leaves the server alone while nothing is running", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const api = seriesApi();
      renderAt("/series/qel");
      await screen.findByRole("heading", { name: "Books (4)" });
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(api.count(DETAIL)).toBe(1);
    });
  });
});

// -- journeys -------------------------------------------------------------------
// README "Build a shared series glossary": a series shares one versioned glossary; each
// book pins a version; the candidate is built from the books at or past the glossary gate.

describe("Series: an editor keeps the Sherlock Holmes novels consistent", () => {
  const HOLMES = "/api/series/sherlock-holmes";
  const book = (job_id: string, volume: number, glossary: string, version: string | null = null) =>
    ({ job_id, volume, added_at: ago(3600), glossary, version });
  const holmes = (overrides: Record<string, unknown> = {}) => ({
    series_id: "sherlock-holmes", name: "Sherlock Holmes", direction: "en>de",
    books: [], versions: [], workbench: null, addable: [], process: null, ...overrides,
  });

  it("starts a series for the novels, English into German, and lands on its empty page", async () => {
    const api = seriesApi({
      "GET /api/series": seriesList({ series: [] }),
      "GET /api/languages": { ...LANGUAGES, languages: [...LANGUAGES.languages, { code: "de", name: "German", tier: "profiled" }] },
      [`GET ${HOLMES}/detail`]: holmes(),
    });
    const user = renderAt("/series");
    expect(await screen.findByText("No series yet.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "+ New series" }));
    const dialog = within(screen.getByRole("dialog", { name: "New series" }));
    await user.type(dialog.getByLabelText("Name"), "Sherlock Holmes");
    expect(dialog.getByLabelText("Series ID")).toHaveValue("sherlock-holmes");
    const into = dialog.getByRole("combobox", { name: "Into language" });
    await user.clear(into);
    await user.type(into, "de");
    await user.tab();
    await user.click(dialog.getByRole("button", { name: "Create series" }));

    expect(await screen.findByText("Sherlock Holmes", { selector: ".title" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/series/sherlock-holmes");
    expect(api.posted("/api/series")).toEqual([{ series_id: "sherlock-holmes", name: "Sherlock Holmes", direction: "en>de" }]);
    expect(screen.getByText("EN → DE")).toBeInTheDocument();
    expect(screen.getByText("no version yet")).toBeInTheDocument();
    expect(screen.getByText("No books yet. Add jobs below.")).toBeInTheDocument();
    expect(screen.getByText("No existing EN → DE jobs outside a series to add.")).toBeInTheDocument();
  });

  it("adds the two novels already translated, builds the candidate glossary, and opens the workbench", async () => {
    const server = { books: [] as ReturnType<typeof book>[], built: false };
    const loose = [
      { job_id: "a-study-in-scarlet", source: "a-study-in-scarlet.epub", glossary: "approved" },
      { job_id: "the-sign-of-the-four", source: "the-sign-of-the-four.epub", glossary: "resolved" },
    ];
    const page = () => holmes({
      books: server.books,
      addable: loose.filter((job) => !server.books.some((b) => b.job_id === job.job_id)),
      workbench: server.built ? { based_on: null, built_at: ago(2), terms: 148, pending: 6, keep: 97 } : null,
    });
    const api = seriesApi({
      [`GET ${HOLMES}/detail`]: page,
      [`POST ${HOLMES}/books`]: (body: { job_ids: string[] }) => {
        server.books = body.job_ids.map((id, i) => book(id, i + 1, loose.find((job) => job.job_id === id)?.glossary ?? ""));
        return page();
      },
      [`POST ${HOLMES}/build`]: () => { server.built = true; return page(); },
      [`GET ${HOLMES}/workbench`]: { ...workbenchView, series_id: "sherlock-holmes" },
    });
    const user = renderAt("/series/sherlock-holmes");
    await screen.findByRole("heading", { name: "Books (0)" });
    expect(screen.getByRole("button", { name: "Build candidate" })).toBeDisabled(); // nothing to build from yet

    // Tick them in reading order: that is the order of the volumes.
    await user.click(screen.getByRole("checkbox", { name: /a-study-in-scarlet/ }));
    await user.click(screen.getByRole("checkbox", { name: /the-sign-of-the-four/ }));
    await user.click(screen.getByRole("button", { name: "Add 2 as next volumes" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Added 2 books.");
    expect(api.posted(`${HOLMES}/books`)).toEqual([{ job_ids: ["a-study-in-scarlet", "the-sign-of-the-four"] }]);
    const volumes = screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell").slice(0, 3).map((cell) => cell.textContent));
    expect(volumes).toEqual([["1", "a-study-in-scarlet", "approved"], ["2", "the-sign-of-the-four", "at glossary gate"]]);
    expect(screen.getByText("2 of 2 books ready.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Build candidate" }));
    expect(await screen.findByText(/^Built/)).toHaveTextContent("148 terms, 97 kept, 6 pending.");

    await user.click(screen.getByRole("tab", { name: "Workbench (6 pending)" }));
    expect(await screen.findByText(/candidate terms/)).toBeInTheDocument();
    expect(api.requested(`${HOLMES}/workbench`)).toBe(true);
  });

  it("checks how the published glossary renders Watson before approving the next volume with it", async () => {
    seriesApi({
      [`GET ${HOLMES}/detail`]: holmes({
        books: [book("a-study-in-scarlet", 1, "approved", "v001"), book("the-sign-of-the-four", 2, "resolved")],
        versions: [{ version: "v001", created_at: ago(5 * 86400), source_jobs: ["a-study-in-scarlet"], term_count: 2, based_on: null }],
      }),
      [`GET ${HOLMES}/version`]: {
        glossary: { entries: [{ source: "Dr. Watson", target: "Dr. Watson", category: "person" }, { source: "Baker Street", target: "Baker Street", category: "place" }] },
        glossary_pair: { pair: "en>de", source: "English", target: "German" },
      },
    });
    const user = renderAt("/series/sherlock-holmes");
    await screen.findByRole("heading", { name: "Books (2)" });
    // The second novel is waiting at its glossary gate, and the page says which version it would take.
    expect(screen.getByRole("link", { name: "approve with v001 →" })).toHaveAttribute("href", "/jobs/the-sign-of-the-four/glossary");

    await user.click(screen.getByRole("tab", { name: "Versions (1)" }));
    expect(within(rowOf("5 d ago")).getAllByRole("cell").map((cell) => cell.textContent)).toEqual([
      "v001", "5 d ago", "2", "a-study-in-scarlet", "a-study-in-scarlet", "Terms",
    ]);
    await user.click(screen.getByRole("button", { name: "Terms" }));
    expect(await screen.findByRole("heading", { name: "v001 terms (2)" })).toBeInTheDocument();
    const terms = screen.getAllByRole("table")[1];
    expect(within(terms).getAllByRole("columnheader").map((th) => th.textContent)).toEqual(["English", "German", "Category"]);
    const watson = within(terms).getAllByRole("row")[1];
    expect(within(watson).getAllByRole("cell").map((cell) => [cell.textContent, cell.getAttribute("lang")])).toEqual([
      ["Dr. Watson", "en"], ["Dr. Watson", "de"], ["person", null],
    ]);
  });

  it("takes a book out of the series again before it is pinned", async () => {
    const api = seriesApi({
      [`GET ${HOLMES}/detail`]: holmes({ books: [book("a-study-in-scarlet", 1, "approved", "v001"), book("the-valley-of-fear", 2, "draft")] }),
      [`POST ${HOLMES}/books/remove`]: holmes({ books: [book("a-study-in-scarlet", 1, "approved", "v001")] }),
    });
    const user = renderAt("/series/sherlock-holmes");
    await screen.findByRole("heading", { name: "Books (2)" });
    // A pinned book stays; only the draft that was added by mistake can go.
    expect(within(rowOf("a-study-in-scarlet")).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    await user.click(within(rowOf("the-valley-of-fear")).getByRole("button", { name: "Remove" }));

    expect(await screen.findByRole("status")).toHaveTextContent("the-valley-of-fear removed.");
    expect(api.posted(`${HOLMES}/books/remove`)).toEqual([{ job_id: "the-valley-of-fear" }]);
    expect(screen.getByRole("heading", { name: "Books (1)" })).toBeInTheDocument();
  });
});
