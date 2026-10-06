import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { jobInfo } from "../test/job";

// -- fixtures -----------------------------------------------------------------

const ago = (seconds: number) => new Date(Date.now() - seconds * 1000).toISOString();

const job = (overrides: Record<string, unknown> = {}) => ({
  job_id: "alice",
  overall: "running",
  source: "alice.epub",
  direction: "en-zh",
  downloadable: false,
  current_stage: "translate",
  completed: 5,
  total: 17,
  updated: ago(10),
  ...overrides,
});

const setup = (jobs: unknown[] = [], overrides: Record<string, unknown> = {}) => ({
  configs: [{ name: "existing.yaml" }],
  config_dir: "configs",
  runs: "D:\\runs",
  template: "configs\\default.yaml",
  jobs,
  ...overrides,
});

const manyJobs = (n: number) => Array.from({ length: n }, (_, i) => job({ job_id: `job-${String(i + 1).padStart(2, "0")}` }));

const BOOK = {
  name: "alice.epub", path: "D:\\runs\\.uploads\\alice.epub", size: 2048, format: "epub",
  title: "Alice's Adventures in Wonderland", authors: ["Lewis Carroll"], language: "en", has_cover: true,
};

function jobsApi(jobs: unknown[] = [], overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/setup": setup(jobs),
    "POST /api/uploads": { path: BOOK.path },
    "GET /api/book": BOOK,
    "GET /api/jobs/suggest": { config: "alice.yaml", job_id: "alice" },
    "POST /api/jobs/new": (body: { job_id: string }) => ({ job_id: body.job_id, created_config: true }),
    ...overrides,
  });
}

function renderJobs() {
  window.history.pushState({}, "", "/");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

const rowOf = (jobId: string) => screen.getByRole("link", { name: jobId }).closest("tr") as HTMLElement;
const epub = (name = "alice.epub") => new File(["book"], name, { type: "application/epub+zip" });

async function openDialog(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "+ Add new job" }));
  return screen.getByRole("dialog", { name: "New translation job" });
}

/** Pick a file through the dialog's hidden file input, as Browse… does. */
const pick = (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement, file: File) =>
  user.upload(dialog.querySelector('input[type="file"]') as HTMLInputElement, file);

afterEach(() => vi.useRealTimers());

// -- tests --------------------------------------------------------------------

describe("Jobs page", () => {
  describe("job list", () => {
    it("says so while loading, and keeps Add disabled until the setup is known", async () => {
      const gate = deferred();
      jobsApi([], { "GET /api/setup": () => gate.promise });
      renderJobs();
      expect(screen.getByText("Loading…")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "+ Add new job" })).toBeDisabled();

      gate.resolve(setup());
      expect(await screen.findByText(/No jobs yet in/)).toHaveTextContent("No jobs yet in D:\\runs.");
      expect(screen.getByRole("button", { name: "+ Add new job" })).toBeEnabled();
      expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });

    it("lists each job with its source, direction, status, stage, progress, and age", async () => {
      jobsApi([job({ updated: ago(45 * 60) })]);
      renderJobs();
      const row = within(await screen.findByRole("row", { name: /alice/ }));
      expect(row.getByRole("link", { name: "alice" })).toHaveAttribute("href", "/jobs/alice/progress");
      expect(row.getByText("alice.epub")).toBeInTheDocument();
      expect(row.getByText("EN → ZH")).toHaveAttribute("title", "en-zh");
      expect(row.getByText("running")).toBeInTheDocument();
      expect(row.getByText("Translate")).toBeInTheDocument();
      expect(row.getByText("5/17 stages")).toBeInTheDocument();
      expect(row.getByText("45 min ago")).toBeInTheDocument();
      expect(screen.getByText("1–1 of 1 jobs")).toBeInTheDocument();
    });

    it.each(["draft", "starting"])("opens a %s job on its Config tab and shows it as not started", async (overall) => {
      jobsApi([job({ job_id: "my book", overall, current_stage: "", completed: 0, direction: "" })]);
      renderJobs();
      await screen.findByRole("link", { name: "my book" });
      expect(screen.getByRole("link", { name: "my book" })).toHaveAttribute("href", "/jobs/my%20book/config");
      const row = within(rowOf("my book"));
      expect(row.getByText("not started")).toBeInTheDocument();
      expect(row.getByText("—")).toHaveAttribute("title", "direction not set");
    });

    it("links a job waiting at the glossary gate straight to the Glossary tab", async () => {
      jobsApi([job({ overall: "paused", current_stage: "approve_glossary" })]);
      renderJobs();
      expect(await screen.findByRole("link", { name: "Review glossary →" })).toHaveAttribute("href", "/jobs/alice/glossary");
      expect(within(rowOf("alice")).getByText("Approve glossary")).toBeInTheDocument();
    });

    it("links a job waiting for final review straight to the Final review tab", async () => {
      jobsApi([job({ overall: "paused", current_stage: "compile" })]);
      renderJobs();
      expect(await screen.findByRole("link", { name: "Open final review →" })).toHaveAttribute("href", "/jobs/alice/review");
    });

    it("has no shortcut for a job paused anywhere else, or running at a gate stage", async () => {
      jobsApi([job({ job_id: "a", overall: "paused", current_stage: "translate" }), job({ job_id: "b", overall: "running", current_stage: "compile" })]);
      renderJobs();
      await screen.findByRole("link", { name: "a" });
      expect(screen.queryByRole("link", { name: /→/ })).not.toBeInTheDocument();
    });

    it("shows a dash for a started job with no current stage", async () => {
      jobsApi([job({ overall: "complete", current_stage: "", completed: 17 })]);
      renderJobs();
      await screen.findByRole("link", { name: "alice" });
      expect(within(rowOf("alice")).getByText("—")).toBeInTheDocument();
      expect(within(rowOf("alice")).getByText("17/17 stages")).toBeInTheDocument();
    });

    it("offers the translated book of a finished job", async () => {
      jobsApi([job({ job_id: "my book", overall: "complete", downloadable: true }), job({ job_id: "other" })]);
      renderJobs();
      await screen.findByRole("link", { name: "my book" });
      const links = screen.getAllByRole("link", { name: "⤓ Download" });
      expect(links).toHaveLength(1);
      expect(links[0]).toHaveAttribute("href", "/api/jobs/my%20book/output");
      expect(links[0]).toHaveAttribute("download");
      expect(within(rowOf("other")).queryByRole("link", { name: "⤓ Download" })).not.toBeInTheDocument();
    });

    it("says why the list could not be loaded, and shows it once the server answers", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let down = true;
      jobsApi([], { "GET /api/setup": () => (down ? apiError("the runs folder is not readable", 500) : setup([job()])) });
      renderJobs();
      expect(await screen.findByText("the runs folder is not readable")).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "+ Add new job" })).toBeDisabled();

      down = false;
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(await screen.findByRole("link", { name: "alice" })).toBeInTheDocument();
      expect(screen.queryByText("the runs folder is not readable")).not.toBeInTheDocument();
    });

    it("shows an empty progress bar for a job that has no stages yet", async () => {
      jobsApi([job({ overall: "draft", current_stage: "", completed: 0, total: 0 })]);
      renderJobs();
      await screen.findByRole("link", { name: "alice" });
      expect(within(rowOf("alice")).getByText("0/0 stages")).toBeInTheDocument();
      expect((rowOf("alice").querySelector(".bar i") as HTMLElement).style.width).toBe("0%");
    });

    it("refreshes the list every ten seconds", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let jobs = [job()];
      const api = jobsApi([], { "GET /api/setup": () => setup(jobs) });
      renderJobs();
      await screen.findByRole("link", { name: "alice" });

      jobs = [job(), job({ job_id: "crusoe" })];
      await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
      expect(await screen.findByRole("link", { name: "crusoe" })).toBeInTheDocument();
      expect(api.count("/api/setup")).toBe(2);
    });
  });

  describe("paging", () => {
    const shownIds = () => screen.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("link")[0].textContent);
    const pageInput = () => screen.getByRole("spinbutton", { name: "Page number" });

    it("shows ten jobs a page and steps forward and back", async () => {
      jobsApi(manyJobs(25));
      const user = renderJobs();
      await screen.findByText("1–10 of 25 jobs");
      expect(shownIds()).toEqual(manyJobs(10).map((j) => j.job_id));
      expect(screen.getByText("of 3")).toBeInTheDocument();
      expect(screen.getByTitle("First page")).toBeDisabled();
      expect(screen.getByTitle("Previous page")).toBeDisabled();

      await user.click(screen.getByTitle("Next page"));
      expect(screen.getByText("11–20 of 25 jobs")).toBeInTheDocument();
      expect(shownIds()[0]).toBe("job-11");
      expect(pageInput()).toHaveValue(2);

      await user.click(screen.getByTitle("Previous page"));
      expect(screen.getByText("1–10 of 25 jobs")).toBeInTheDocument();
    });

    it("jumps to the last and first page", async () => {
      jobsApi(manyJobs(25));
      const user = renderJobs();
      await screen.findByText("1–10 of 25 jobs");

      await user.click(screen.getByTitle("Last page"));
      expect(screen.getByText("21–25 of 25 jobs")).toBeInTheDocument();
      expect(shownIds()).toEqual(["job-21", "job-22", "job-23", "job-24", "job-25"]);
      expect(screen.getByTitle("Next page")).toBeDisabled();
      expect(screen.getByTitle("Last page")).toBeDisabled();

      await user.click(screen.getByTitle("First page"));
      expect(screen.getByText("1–10 of 25 jobs")).toBeInTheDocument();
    });

    it("goes to a typed page on Enter, clamped to the pages that exist", async () => {
      jobsApi(manyJobs(25));
      const user = renderJobs();
      await screen.findByText("1–10 of 25 jobs");

      await user.clear(pageInput());
      await user.type(pageInput(), "2{Enter}");
      expect(screen.getByText("11–20 of 25 jobs")).toBeInTheDocument();

      await user.clear(pageInput());
      await user.type(pageInput(), "99{Enter}");
      expect(screen.getByText("21–25 of 25 jobs")).toBeInTheDocument();
      expect(pageInput()).toHaveValue(3);
    });

    it("goes to a typed page when the field loses focus", async () => {
      jobsApi(manyJobs(25));
      const user = renderJobs();
      await screen.findByText("1–10 of 25 jobs");
      await user.clear(pageInput());
      await user.type(pageInput(), "3");
      await user.tab();
      expect(screen.getByText("21–25 of 25 jobs")).toBeInTheDocument();
    });

    it("has a single page for ten jobs or fewer", async () => {
      jobsApi(manyJobs(10));
      renderJobs();
      await screen.findByText("1–10 of 10 jobs");
      expect(screen.getByText("of 1")).toBeInTheDocument();
      expect(screen.getByTitle("Next page")).toBeDisabled();
    });
  });

  describe("new job dialog", () => {
    it("opens empty, with nothing to create yet", async () => {
      jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      expect(within(dialog).getByText(/Drop a book here .*, or/)).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Config file name")).toHaveValue("");
      expect(within(dialog).getByText("Pick a source to get a suggested name.")).toBeInTheDocument();
      expect(within(dialog).getByLabelText("Job ID")).toHaveValue("");
      expect(within(dialog).getByRole("button", { name: "Create job" })).toBeDisabled();
    });

    it.each([
      ["Cancel", (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) => user.click(within(dialog).getByRole("button", { name: "Cancel" }))],
      ["Escape", (user: ReturnType<typeof userEvent.setup>) => user.keyboard("{Escape}")],
      ["a click on the backdrop", (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) => user.click(dialog.parentElement as HTMLElement)],
    ])("closes on %s", async (_how, close) => {
      jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      await close(user, dialog);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("stays open on a click inside it", async () => {
      jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      await user.click(within(dialog).getByText("Pick a source to get a suggested name."));
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("uploads the picked book, shows it, and suggests the config name and job id", async () => {
      const api = jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());

      expect(await within(dialog).findByText(BOOK.title)).toBeInTheDocument();
      expect(within(dialog).getByText("Lewis Carroll")).toBeInTheDocument();
      await waitFor(() => expect(within(dialog).getByLabelText("Config file name")).toHaveValue("alice.yaml"));
      // The job id follows the config name a moment later.
      await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("alice"));
      expect(within(dialog).getByText("A new alice.yaml is created in configs from default.yaml; you adjust it on the Config tab.")).toBeInTheDocument();
      expect(within(dialog).getByText("Defaults to the config file name. Used as the workspace folder name.")).toBeInTheDocument();

      expect(api.calls.find((c) => c.method === "POST")?.path).toBe("/api/uploads?name=alice.epub");
      expect(api.requested(`/api/book?path=${encodeURIComponent(BOOK.path)}`)).toBe(true);
      expect(api.requested(`/api/jobs/suggest?source=${encodeURIComponent(BOOK.path)}`)).toBe(true);
    });

    it("says it is uploading while the book is on its way", async () => {
      const gate = deferred();
      jobsApi([], { "POST /api/uploads": () => gate.promise });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());

      expect(within(dialog).getByText("Uploading and reading the book…")).toBeInTheDocument();
      expect(within(dialog).queryByRole("button", { name: "Browse…" })).not.toBeInTheDocument();
      gate.resolve({ path: BOOK.path });
      expect(await within(dialog).findByText(BOOK.title)).toBeInTheDocument();
    });

    it("takes a book dropped onto the drop zone", async () => {
      const api = jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      const zone = within(dialog).getByText(/Drop a book here .*, or/);

      fireEvent.dragOver(zone);
      expect(zone).toHaveClass("over");
      fireEvent.dragLeave(zone);
      expect(zone).not.toHaveClass("over");

      fireEvent.drop(zone, { dataTransfer: { files: [epub("crusoe.RTF")] } });
      expect(await within(dialog).findByText(BOOK.title)).toBeInTheDocument();
      expect(api.requested("/api/uploads?name=crusoe.RTF")).toBe(true);
    });

    it("refuses a PDF, which is not a book it can read, without uploading it", async () => {
      const api = jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      fireEvent.drop(within(dialog).getByText(/Drop a book here .*, or/), { dataTransfer: { files: [new File(["x"], "notes.pdf")] } });

      expect(await screen.findByRole("status")).toHaveTextContent("Choose an EPUB, RTF, text, Markdown, HTML, or Word (.docx) file.");
      expect(api.calls.some((c) => c.path.startsWith("/api/uploads"))).toBe(false);
      expect(within(dialog).getByText(/Drop a book here .*, or/)).toBeInTheDocument();
    });

    it("shows why an upload failed and lets the user try again", async () => {
      jobsApi([], { "POST /api/uploads": apiError("not a valid EPUB: missing container.xml", 422) });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());

      expect(await screen.findByRole("status")).toHaveTextContent("not a valid EPUB: missing container.xml");
      expect(within(dialog).getByRole("button", { name: "Browse…" })).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Create job" })).toBeDisabled();
    });

    it("replaces the book when another one is chosen or dropped onto the card", async () => {
      const second = { ...BOOK, name: "crusoe.epub", path: "D:\\runs\\.uploads\\crusoe.epub", title: "Robinson Crusoe", authors: ["Daniel Defoe"] };
      const api = jobsApi([], {
        "POST /api/uploads": (_: unknown, url: URL) => ({ path: url.searchParams.get("name") === "crusoe.epub" ? second.path : BOOK.path }),
        "GET /api/book": (_: unknown, url: URL) => (url.searchParams.get("path") === second.path ? second : BOOK),
        "GET /api/jobs/suggest": (_: unknown, url: URL) =>
          url.searchParams.get("source") === second.path ? { config: "crusoe.yaml", job_id: "crusoe" } : { config: "alice.yaml", job_id: "alice" },
      });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      await within(dialog).findByText(BOOK.title);
      expect(within(dialog).getByRole("button", { name: "Choose another book…" })).toBeInTheDocument();

      fireEvent.drop(within(dialog).getByText(BOOK.title), { dataTransfer: { files: [epub("crusoe.epub")] } });
      expect(await within(dialog).findByText("Robinson Crusoe")).toBeInTheDocument();
      await waitFor(() => expect(within(dialog).getByLabelText("Config file name")).toHaveValue("crusoe.yaml"));
      await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("crusoe"));
      expect(api.requested("/api/uploads?name=crusoe.epub")).toBe(true);
    });

    it("creates the job and opens its Config tab", async () => {
      const api = jobsApi([], { "GET /api/jobs/alice/info": jobInfo({ job_id: "alice", kind: "draft", overall: "draft" }) });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const create = within(dialog).getByRole("button", { name: "Create job" });
      await waitFor(() => expect(create).toBeEnabled());

      await user.click(create);
      await waitFor(() => expect(window.location.pathname).toBe("/jobs/alice/config"));
      expect(api.posted("/api/jobs/new")).toEqual([{ source: BOOK.path, config: "alice.yaml", job_id: "alice", series_id: "" }]);
      expect(screen.getByRole("status")).toHaveTextContent("Created alice.yaml from default.yaml.");
    });

    it("says when the job reuses a config file that already exists", async () => {
      jobsApi([], {
        "GET /api/jobs/suggest": { config: "existing.yaml", job_id: "existing" },
        "POST /api/jobs/new": { job_id: "existing", created_config: false },
        "GET /api/jobs/existing/info": jobInfo({ job_id: "existing", kind: "draft", overall: "draft" }),
      });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      expect(await within(dialog).findByText("existing.yaml already exists in configs; the job uses it as is.")).toBeInTheDocument();

      await user.click(within(dialog).getByRole("button", { name: "Create job" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Using existing existing.yaml.");
    });

    it("derives the job id from an edited config name", async () => {
      jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const config = within(dialog).getByLabelText("Config file name");
      await waitFor(() => expect(config).toHaveValue("alice.yaml"));

      await user.clear(config);
      await user.type(config, "wonderland.yml");
      await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("wonderland"));
    });

    it("keeps a job id the user typed when the config name changes", async () => {
      const api = jobsApi([], { "GET /api/jobs/first-try/info": jobInfo({ job_id: "first-try", kind: "draft", overall: "draft" }) });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const config = within(dialog).getByLabelText("Config file name");
      const id = within(dialog).getByLabelText("Job ID");
      await waitFor(() => expect(id).toHaveValue("alice"));

      await user.clear(id);
      await user.type(id, "first-try");
      await user.clear(config);
      await user.type(config, "wonderland.yaml");
      expect(id).toHaveValue("first-try");

      await user.click(within(dialog).getByRole("button", { name: "Create job" }));
      await waitFor(() => expect(api.posted("/api/jobs/new")).toEqual([
        { source: BOOK.path, config: "wonderland.yaml", job_id: "first-try", series_id: "" },
      ]));
    });

    it.each(["notes.txt", "my book.yaml", ".hidden.yaml", "sub/dir.yaml"])("rejects the config name %j", async (name) => {
      jobsApi();
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const config = within(dialog).getByLabelText("Config file name");
      await waitFor(() => expect(config).toHaveValue("alice.yaml"));

      await user.clear(config);
      await user.type(config, name);
      expect(within(dialog).getByText("Use a simple file name ending in .yaml.")).toBeInTheDocument();
      expect(within(dialog).getByRole("button", { name: "Create job" })).toBeDisabled();
    });

    it("will not create a job whose id is taken or empty", async () => {
      jobsApi([job({ job_id: "alice" })]);
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const id = within(dialog).getByLabelText("Job ID");
      await waitFor(() => expect(id).toHaveValue("alice"));

      const create = within(dialog).getByRole("button", { name: "Create job" });
      expect(within(dialog).getByText("A job with this ID already exists.")).toBeInTheDocument();
      expect(create).toBeDisabled();

      await user.type(id, "-2");
      expect(within(dialog).queryByText("A job with this ID already exists.")).not.toBeInTheDocument();
      expect(create).toBeEnabled();

      await user.clear(id);
      expect(create).toBeDisabled();
    });

    it("shows why creating failed and stays open for another try", async () => {
      jobsApi([], { "POST /api/jobs/new": apiError("job id may only contain letters, digits, dot, dash, underscore", 400) });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      const create = within(dialog).getByRole("button", { name: "Create job" });
      await waitFor(() => expect(create).toBeEnabled());

      await user.click(create);
      expect(await screen.findByRole("status")).toHaveTextContent("job id may only contain letters, digits, dot, dash, underscore");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
      expect(create).toBeEnabled();
      expect(window.location.pathname).toBe("/");
    });

    it("says when no names could be suggested, and still creates the job with names typed by hand", async () => {
      const api = jobsApi([], {
        "GET /api/jobs/suggest": apiError("the book has no readable title", 500),
        "GET /api/jobs/wonderland/info": jobInfo({ job_id: "wonderland", kind: "draft", overall: "draft" }),
      });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());

      expect(await screen.findByRole("status")).toHaveTextContent("No name could be suggested: the book has no readable title");
      expect(within(dialog).getByLabelText("Config file name")).toHaveValue("");
      await user.type(within(dialog).getByLabelText("Config file name"), "wonderland.yaml");
      await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("wonderland"));

      await user.click(within(dialog).getByRole("button", { name: "Create job" }));
      await waitFor(() => expect(api.posted("/api/jobs/new")).toEqual([
        { source: BOOK.path, config: "wonderland.yaml", job_id: "wonderland", series_id: "" },
      ]));
    });

    it("names an empty template when the setup has none", async () => {
      jobsApi([], { "GET /api/setup": setup([], { template: "" }) });
      const user = renderJobs();
      const dialog = await openDialog(user);
      await pick(user, dialog, epub());
      expect(await within(dialog).findByText("A new alice.yaml is created in configs from an empty config; you adjust it on the Config tab.")).toBeInTheDocument();
    });
  });
});

// -- journeys -------------------------------------------------------------------
// What a translator does on this page, start to finish (README: "Configure and start").

describe("Jobs page: a translator's day", () => {
  const SIGN = {
    name: "the-sign-of-the-four.epub", path: "D:\\runs\\.uploads\\the-sign-of-the-four.epub", size: 412_000, format: "epub",
    title: "The Sign of the Four", authors: ["Arthur Conan Doyle"], language: "en", has_cover: true,
  };
  const signApi = (jobs: unknown[] = [], overrides: Record<string, unknown> = {}) =>
    jobsApi(jobs, {
      "POST /api/uploads": { path: SIGN.path },
      "GET /api/book": SIGN,
      "GET /api/jobs/suggest": { config: "the-sign-of-the-four.yaml", job_id: "the-sign-of-the-four" },
      "GET /api/jobs/the-sign-of-the-four/info": jobInfo({ job_id: "the-sign-of-the-four", kind: "draft", overall: "draft" }),
      ...overrides,
    });
  const signEpub = () => epub("the-sign-of-the-four.epub");

  it("adds The Sign of the Four, keeps the suggested names, and is taken to its Config tab", async () => {
    const api = signApi();
    const user = renderJobs();
    expect(await screen.findByText(/No jobs yet/)).toBeInTheDocument();

    const dialog = await openDialog(user);
    await pick(user, dialog, signEpub());
    // The dialog shows which book it read, so a wrong file is caught before anything is created.
    expect(await within(dialog).findByText("The Sign of the Four")).toBeInTheDocument();
    expect(within(dialog).getByText("Arthur Conan Doyle")).toBeInTheDocument();
    expect(within(dialog).getByText("402 KB")).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByLabelText("Job ID")).toHaveValue("the-sign-of-the-four"));
    expect(within(dialog).getByText(/A new the-sign-of-the-four\.yaml is created in configs from default\.yaml/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Create job" }));
    await waitFor(() => expect(window.location.pathname).toBe("/jobs/the-sign-of-the-four/config"));
    expect(screen.getByRole("status")).toHaveTextContent("Created the-sign-of-the-four.yaml from default.yaml.");
    expect(api.posted("/api/jobs/new")).toEqual([
      { source: SIGN.path, config: "the-sign-of-the-four.yaml", job_id: "the-sign-of-the-four", series_id: "" },
    ]);
  });

  it("comes back in the morning, sees which book is waiting for them, and goes straight to its glossary", async () => {
    signApi([
      job({ job_id: "alice-en-zh", source: "alice.epub", direction: "en-zh", overall: "complete", current_stage: "", completed: 17, downloadable: true, updated: ago(9 * 3600) }),
      job({ job_id: "sign-of-four-en-de", source: "the-sign-of-the-four.epub", direction: "en>de", overall: "paused", current_stage: "approve_glossary", completed: 3, updated: ago(40 * 60) }),
      job({ job_id: "ah-q-zh-ja", source: "阿Q正传.epub", direction: "zh>ja", overall: "running", current_stage: "translate", completed: 6, updated: ago(20) }),
    ], { "GET /api/jobs/sign-of-four-en-de/info": jobInfo({ job_id: "sign-of-four-en-de", overall: "paused" }) });
    const user = renderJobs();

    // One glance tells each book's languages and where it stands.
    await screen.findByRole("link", { name: "alice-en-zh" });
    const glance = (id: string) => within(rowOf(id)).getAllByRole("cell").slice(1, 5).map((cell) => cell.textContent);
    expect(glance("alice-en-zh")).toEqual(["alice.epub", "EN → ZH", "complete", "—"]);
    expect(glance("sign-of-four-en-de")).toEqual(["the-sign-of-the-four.epub", "EN → DE", "paused", "Approve glossaryReview glossary →"]);
    expect(glance("ah-q-zh-ja")).toEqual(["阿Q正传.epub", "ZH → JA", "running", "Translate"]);
    expect(within(rowOf("alice-en-zh")).getByText("9 h ago")).toBeInTheDocument();
    expect(within(rowOf("ah-q-zh-ja")).getByText("just now")).toBeInTheDocument();

    // The finished book can be downloaded right here; the paused one needs a person.
    expect(within(rowOf("alice-en-zh")).getByRole("link", { name: "⤓ Download" })).toHaveAttribute("href", "/api/jobs/alice-en-zh/output");
    await user.click(screen.getByRole("link", { name: "Review glossary →" }));
    expect(window.location.pathname).toBe("/jobs/sign-of-four-en-de/glossary");
  });

  it("drops a PDF by mistake, is told what the page takes, and drops the EPUB instead", async () => {
    const api = signApi();
    const user = renderJobs();
    const dialog = await openDialog(user);
    const zone = within(dialog).getByText(/Drop a book here .*, or/);

    fireEvent.drop(zone, { dataTransfer: { files: [new File(["%PDF"], "the-sign-of-the-four.pdf")] } });
    expect(await screen.findByRole("status")).toHaveTextContent("Choose an EPUB, RTF, text, Markdown, HTML, or Word (.docx) file.");
    expect(within(dialog).getByRole("button", { name: "Create job" })).toBeDisabled();

    fireEvent.drop(zone, { dataTransfer: { files: [signEpub()] } });
    expect(await within(dialog).findByText("The Sign of the Four")).toBeInTheDocument();
    await waitFor(() => expect(within(dialog).getByRole("button", { name: "Create job" })).toBeEnabled());
    expect(api.calls.filter((call) => call.path.startsWith("/api/uploads")).map((call) => call.path)).toEqual([
      "/api/uploads?name=the-sign-of-the-four.epub",
    ]);
  });

  it("translates the same book a second time and gives the new job its own name", async () => {
    const api = signApi([job({ job_id: "the-sign-of-the-four", source: "the-sign-of-the-four.epub", overall: "complete" })], {
      "GET /api/setup": setup(
        [job({ job_id: "the-sign-of-the-four", source: "the-sign-of-the-four.epub", overall: "complete" })],
        { configs: [{ name: "the-sign-of-the-four.yaml" }] },
      ),
      "POST /api/jobs/new": { job_id: "the-sign-of-the-four-de", created_config: false },
      "GET /api/jobs/the-sign-of-the-four-de/info": jobInfo({ job_id: "the-sign-of-the-four-de", kind: "draft", overall: "draft" }),
    });
    const user = renderJobs();
    const dialog = await openDialog(user);
    await pick(user, dialog, signEpub());

    // The suggested names collide with the first job: the page says so and will not create it.
    expect(await within(dialog).findByText("A job with this ID already exists.")).toBeInTheDocument();
    expect(within(dialog).getByText("the-sign-of-the-four.yaml already exists in configs; the job uses it as is.")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Create job" })).toBeDisabled();

    await user.type(within(dialog).getByLabelText("Job ID"), "-de");
    await user.click(within(dialog).getByRole("button", { name: "Create job" }));
    await waitFor(() => expect(window.location.pathname).toBe("/jobs/the-sign-of-the-four-de/config"));
    expect(screen.getByRole("status")).toHaveTextContent("Using existing the-sign-of-the-four.yaml.");
    expect(api.posted("/api/jobs/new")).toEqual([
      { source: SIGN.path, config: "the-sign-of-the-four.yaml", job_id: "the-sign-of-the-four-de", series_id: "" },
    ]);
  });

  it("looks for a book translated long ago on the last page of a long list and opens it", async () => {
    const shelf = [...manyJobs(24), job({ job_id: "alice-en-zh", overall: "complete", updated: ago(40 * 86400) })];
    signApi(shelf, { "GET /api/jobs/alice-en-zh/info": jobInfo({ job_id: "alice-en-zh" }) });
    const user = renderJobs();
    await screen.findByText("1–10 of 25 jobs");
    expect(screen.queryByRole("link", { name: "alice-en-zh" })).not.toBeInTheDocument();

    await user.click(screen.getByTitle("Last page"));
    expect(within(rowOf("alice-en-zh")).getByText("40 d ago")).toBeInTheDocument();
    await user.click(screen.getByRole("link", { name: "alice-en-zh" }));
    expect(window.location.pathname).toBe("/jobs/alice-en-zh/progress");
  });
});
