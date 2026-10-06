import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { jobInfo, stage } from "../test/job";

// -- fixtures -----------------------------------------------------------------

const todayAt = (hours: number, minutes: number) => {
  const at = new Date();
  at.setHours(hours, minutes, 0, 0);
  return at.toISOString();
};

/** A run stopped by a failure in the audit: two stages done, one failed, two to go. */
const STAGES = [
  stage("decompile", "completed", { attempts: 1, updated_at: todayAt(9, 5) }),
  stage("translate", "completed", { attempts: 2 }),
  stage("audit_translation", "failed", { attempts: 1, message: "Ollama timed out" }),
  stage("compile", "pending"),
  stage("validate_epub", "pending"),
];

const activity = (overrides: Record<string, unknown> = {}) => ({
  tasks: 3,
  counter: [4, 10],
  segments: null,
  llm_calls: 1234,
  output_tokens: 56789,
  avg_call_seconds: 3.2,
  first: "2026-09-28T10:00:00Z",
  last: "2026-09-28T10:11:39Z",
  ...overrides,
});

const snapshot = (overrides: Record<string, unknown> = {}, progress: Record<string, unknown> = {}) => ({
  status: { job_id: "demo", overall: "failed", stages: STAGES, configuration: { source_path: "" } },
  source: "book.epub",
  process: null,
  progress: { log: "session-1.log", line_count: 0, lines: [], stages: {}, ...progress },
  ...overrides,
});

const running = (stages = [stage("decompile", "completed"), stage("translate", "running", { attempts: 1, message: "chunk 4 of 10" }), stage("compile", "pending")]) =>
  ({ job_id: "demo", overall: "running", stages, configuration: { source_path: "" } });

const estimate = (overrides: Record<string, unknown> = {}) => ({
  elapsed_seconds: 600,
  current: null,
  pending: [],
  unknown_stages: [],
  remaining_seconds: null,
  history_jobs: 0,
  segments: null,
  excludes: [],
  complete: false,
  ...overrides,
});

function progressApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/jobs/demo/info": jobInfo({ overall: "failed", stages: STAGES }),
    "GET /api/jobs/demo/progress": snapshot(),
    "GET /api/jobs/demo/estimate": estimate({ complete: true }),
    "POST /api/jobs/demo/resume": { ok: true },
    "POST /api/jobs/demo/rerun": { ok: true },
    ...overrides,
  });
}

function renderProgressTab() {
  window.history.pushState({}, "", "/jobs/demo/progress");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

/** The pipeline table row of a stage, found by its raw name. */
const rowOf = (name: string) => screen.getByText(name, { selector: "td span" }).closest("tr") as HTMLElement;
const cells = (name: string) => within(rowOf(name)).getAllByRole("cell");
const pipeline = async () => (await screen.findByRole("heading", { name: "Pipeline" })).closest("section") as HTMLElement;
const logBox = () => document.querySelector(".logview") as HTMLElement;

afterEach(() => vi.useRealTimers());

// -- tests --------------------------------------------------------------------

describe("Progress tab", () => {
  describe("summary", () => {
    it("tells a draft job to start first and asks for no progress", async () => {
      const api = progressApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "draft" }) });
      renderProgressTab();
      expect(await screen.findByText(/there is no progress to show/)).toBeInTheDocument();
      expect(api.calls.some((call) => call.path.startsWith("/api/jobs/demo/progress"))).toBe(false);
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    });

    it("shows the stage the run is at, its message, and how many stages are done", async () => {
      progressApi();
      renderProgressTab();
      const summary = within((await screen.findByText("2 of 5 stages complete")).closest("section") as HTMLElement);
      expect(summary.getByText("failed")).toBeInTheDocument();
      expect(summary.getByText("book.epub")).toBeInTheDocument();
      expect(summary.getByText("Audit translation")).toBeInTheDocument();
      expect(summary.getByText("Ollama timed out")).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    });

    it("says so when every stage is complete", async () => {
      const done = STAGES.map((s) => ({ ...s, status: "completed", message: "" }));
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ stages: done }),
        "GET /api/jobs/demo/progress": snapshot({ status: { job_id: "demo", overall: "complete", stages: done, configuration: { source_path: "" } } }),
      });
      renderProgressTab();
      expect(await screen.findByText("All stages complete")).toBeInTheDocument();
      expect(screen.getByText("5 of 5 stages complete")).toBeInTheDocument();
    });

    it("says why the progress could not be loaded", async () => {
      progressApi({ "GET /api/jobs/demo/progress": apiError("workspace is locked", 500) });
      renderProgressTab();
      expect(await screen.findByText("workspace is locked")).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Pipeline" })).not.toBeInTheDocument();
    });

    it("names the dashboard-launched command and how it ended", async () => {
      progressApi({
        "GET /api/jobs/demo/progress": snapshot({
          process: { label: "resume", running: false, exit_code: 0, outcome: "paused", started: "10:00:12", output_tail: "" },
        }),
      });
      renderProgressTab();
      expect(await screen.findByText(/Dashboard-launched/)).toHaveTextContent("Dashboard-launched resume started 10:00:12 — paused, waiting for you");
      expect(screen.queryByText(/command failed/)).not.toBeInTheDocument();
    });

    it("shows the output of a command that failed", async () => {
      progressApi({
        "GET /api/jobs/demo/progress": snapshot({
          process: { label: "resume", running: false, exit_code: 3, outcome: "failed", started: "10:00:12", output_tail: "Traceback: connection refused" },
        }),
      });
      renderProgressTab();
      expect(await screen.findByText(/Dashboard-launched/)).toHaveTextContent("resume started 10:00:12 — failed");
      expect(screen.getByText(/command failed/)).toHaveTextContent("The resume command failed (exit code 3):Traceback: connection refused");
    });

    it.each([
      ["the glossary gate", "approve_glossary", "Review glossary", "/jobs/demo/glossary"],
      ["the final review", "compile", "Open final review", "/jobs/demo/review"],
    ])("links to the tab that is waiting at %s", async (_gate, name, label, href) => {
      const stages = [stage("decompile", "completed"), stage(name, "paused")];
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "paused", stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: { job_id: "demo", overall: "paused", stages, configuration: { source_path: "" } } }),
      });
      renderProgressTab();
      expect((await screen.findByRole("button", { name: label })).closest("a")).toHaveAttribute("href", href);
    });

    it("has no review shortcut when no gate is waiting", async () => {
      progressApi();
      renderProgressTab();
      await pipeline();
      expect(screen.queryByRole("button", { name: /Review glossary|Open final review/ })).not.toBeInTheDocument();
    });
  });

  describe("what the model is doing now", () => {
    const live = (progress: Record<string, unknown>) =>
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages: running().stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: running() }, progress),
      });

    it("shows the latest LLM call and its speed while the run is live", async () => {
      live({ last_llm: "translate chunk 4/10 — streaming 312 tokens", last_rate: 41.5 });
      renderProgressTab();
      const now = (await screen.findByRole("heading", { name: "Now" })).closest("section") as HTMLElement;
      expect(now).toHaveTextContent("translate chunk 4/10 — streaming 312 tokens");
      expect(now).toHaveTextContent("last completed call: 41.5 tok/s");
    });

    it("leaves out the speed until a call has completed", async () => {
      live({ last_llm: "translate chunk 1/10" });
      renderProgressTab();
      const now = (await screen.findByRole("heading", { name: "Now" })).closest("section") as HTMLElement;
      expect(now).not.toHaveTextContent("last completed call");
    });

    it("is hidden once the run is no longer live", async () => {
      progressApi({ "GET /api/jobs/demo/progress": snapshot({}, { last_llm: "translate chunk 4/10" }) });
      renderProgressTab();
      await pipeline();
      expect(screen.queryByRole("heading", { name: "Now" })).not.toBeInTheDocument();
    });
  });

  describe("pipeline table", () => {
    it("lists every stage with its label, raw name, and message", async () => {
      progressApi();
      renderProgressTab();
      const table = within(await pipeline());
      expect(table.getAllByRole("row").slice(1).map((row) => within(row).getAllByRole("cell")[1].querySelector("b")?.textContent)).toEqual([
        "Decompile source", "Translate", "Audit translation", "Compile EPUB", "Validate EPUB",
      ]);
      expect(within(rowOf("audit_translation")).getByText("Ollama timed out")).toBeInTheDocument();
      expect(cells("decompile")[0]).toHaveTextContent("✓");
      expect(cells("audit_translation")[0]).toHaveTextContent("✕");
      expect(cells("compile")[0]).toHaveTextContent("○");
    });

    it("shows this session's work, calls, tokens, time, and attempts for a stage", async () => {
      progressApi({ "GET /api/jobs/demo/progress": snapshot({}, { stages: { translate: activity() } }) });
      renderProgressTab();
      await pipeline();
      const [, , work, calls, tokens, time, , attempts] = cells("translate");
      expect(work).toHaveTextContent("3 LLM tasks · unit 4/10");
      expect(calls).toHaveTextContent(`${(1234).toLocaleString()}avg 3.2s`);
      expect(tokens).toHaveTextContent((56789).toLocaleString());
      expect(time).toHaveTextContent("11m 39s");
      expect(attempts).toHaveTextContent("2");
    });

    it("counts segments when a stage has no unit counter, and one task in the singular", async () => {
      progressApi({
        "GET /api/jobs/demo/progress": snapshot({}, { stages: { translate: activity({ tasks: 1, counter: null, segments: [7, 20], avg_call_seconds: null }) } }),
      });
      renderProgressTab();
      await pipeline();
      expect(cells("translate")[2]).toHaveTextContent("1 LLM task · segment 7/20");
      expect(cells("translate")[3]).not.toHaveTextContent("avg");
    });

    it("leaves the work columns empty for a stage with no activity this session", async () => {
      progressApi();
      renderProgressTab();
      await pipeline();
      const [, , work, calls, tokens, time, , attempts] = cells("compile");
      for (const cell of [work, calls, tokens, time, attempts]) expect(cell).toBeEmptyDOMElement();
    });

    it("dates the last change of a stage that has run, and nothing for one that has not", async () => {
      progressApi();
      renderProgressTab();
      await pipeline();
      expect(cells("decompile")[8]).toHaveTextContent("done 09:05");
      expect(cells("compile")[8]).toBeEmptyDOMElement();
    });

    it("explains a stage in its tooltip, with the live result", async () => {
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages: running().stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: running() }, { stages: { translate: activity({ llm_calls: 1 }) } }),
      });
      const user = renderProgressTab();
      await pipeline();
      await user.hover(within(rowOf("translate")).getByRole("button", { name: "About this stage" }));
      const tip = screen.getByRole("tooltip");
      expect(tip).toHaveTextContent("First-draft translation of every chunk");
      expect(within(tip).getByText("Result").nextElementSibling).toHaveTextContent(
        "Running · 4/10 units · 1 LLM call, avg 3.2s · 11m 39s this session. chunk 4 of 10",
      );
    });

    it("gives a plain result for a stage that has not started", async () => {
      progressApi();
      const user = renderProgressTab();
      await pipeline();
      await user.hover(within(rowOf("compile")).getByRole("button", { name: "About this stage" }));
      expect(within(screen.getByRole("tooltip")).getByText("Result").nextElementSibling).toHaveTextContent("Not started yet.");
    });
  });

  describe("time estimate", () => {
    const withEstimate = (value: unknown, live = false) =>
      progressApi(live
        ? {
            "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages: running().stages }),
            "GET /api/jobs/demo/progress": snapshot({ status: running() }),
            "GET /api/jobs/demo/estimate": value,
          }
        : { "GET /api/jobs/demo/estimate": value });

    it("says how long is left and how the number was made", async () => {
      withEstimate(estimate({
        remaining_seconds: 4778,
        current: { stage: "translate", done: 4, total: 10, seconds_per_unit: 95, remaining_seconds: 570, basis: "95 s per unit so far", running: true },
        pending: [{ stage: "compile", seconds: 300 }],
        unknown_stages: ["validate_epub"],
        history_jobs: 3,
        segments: 4200,
        excludes: ["approve_glossary", "final review of 12 segments"],
      }), true);
      renderProgressTab();
      const panel = (await screen.findByText(/of pipeline time so far/)).closest(".estimate") as HTMLElement;
      expect(panel).toHaveTextContent("≈ 1 h 20 min left");
      expect(panel).not.toHaveTextContent("once resumed");
      expect(panel).toHaveTextContent("10 min of pipeline time so far");
      expect(within(panel).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
        "Translate: ~10 min (4/10 units done; 95 s per unit so far)",
        `Later stages: ~5 min, from 3 previous jobs scaled to ${(4200).toLocaleString()} segments`,
        "Not estimated (no history yet): Validate EPUB",
        "Excludes waiting for you: Approve glossary; final review of 12 segments",
      ]);
    });

    it("puts each stage's share in the Estimate column", async () => {
      const stages = [
        stage("decompile", "completed"), stage("approve_glossary", "pending"), stage("translate", "running"),
        stage("audit_translation", "pending"), stage("compile", "pending"), stage("validate_epub", "pending"),
      ];
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: running(stages) }),
        "GET /api/jobs/demo/estimate": estimate({
          remaining_seconds: 870,
          current: { stage: "translate", done: 4, total: 10, seconds_per_unit: 95, remaining_seconds: 570, basis: "", running: true },
          pending: [{ stage: "compile", seconds: 300 }, { stage: "audit_translation", seconds: 0.4 }],
          unknown_stages: ["validate_epub"],
          excludes: ["approve_glossary"],
        }),
      });
      renderProgressTab();
      await screen.findByText(/of pipeline time so far/);
      const estimateOf = (name: string) => cells(name)[6].textContent;
      expect(estimateOf("decompile")).toBe(""); // done
      expect(estimateOf("approve_glossary")).toBe("your review");
      expect(estimateOf("translate")).toBe("~10 min left");
      expect(estimateOf("audit_translation")).toBe(""); // under a second: not worth a number
      expect(estimateOf("compile")).toBe("~5 min");
      expect(estimateOf("validate_epub")).toBe("?");
    });

    it("says the time counts from when a stopped run is resumed", async () => {
      withEstimate(estimate({ remaining_seconds: 45, pending: [{ stage: "compile", seconds: 45 }], history_jobs: 1 }));
      renderProgressTab();
      const panel = (await screen.findByText(/of pipeline time so far/)).closest(".estimate") as HTMLElement;
      expect(panel).toHaveTextContent("≈ 45 s left once resumed");
      expect(panel).toHaveTextContent("Later stages: ~45 s, from 1 previous job");
      expect(panel).not.toHaveTextContent("scaled to");
    });

    it("admits when there is not enough data", async () => {
      withEstimate(estimate());
      renderProgressTab();
      expect(await screen.findByText("Not enough data to estimate yet")).toBeInTheDocument();
      expect(screen.queryByRole("listitem")).not.toBeInTheDocument();
    });

    it("shows no estimate panel once the estimate says the job is complete", async () => {
      withEstimate(estimate({ complete: true, remaining_seconds: 0 }));
      renderProgressTab();
      await pipeline();
      expect(screen.queryByText(/of pipeline time so far/)).not.toBeInTheDocument();
    });

    it("shows no estimate when one cannot be made", async () => {
      withEstimate(apiError("no history", 500));
      renderProgressTab();
      await pipeline();
      expect(screen.queryByText(/of pipeline time so far/)).not.toBeInTheDocument();
      expect(cells("compile")[6]).toBeEmptyDOMElement();
    });
  });

  describe("stage actions", () => {
    const actions = (name: string) => within(rowOf(name)).queryAllByRole("button").map((b) => b.textContent).filter((text) => text !== "ⓘ");

    it("offers resume and rerun on the failed stage, rerun on finished ones, nothing on pending ones", async () => {
      progressApi();
      renderProgressTab();
      await pipeline();
      expect(actions("decompile")).toEqual(["Rerun from here"]);
      expect(actions("audit_translation")).toEqual(["Resume", "Rerun"]);
      expect(actions("compile")).toEqual([]);
    });

    it("offers nothing on a gate that is waiting for a person", async () => {
      const stages = [stage("decompile", "completed"), stage("approve_glossary", "paused")];
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "paused", stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: { job_id: "demo", overall: "paused", stages, configuration: { source_path: "" } } }),
      });
      renderProgressTab();
      await pipeline();
      expect(actions("approve_glossary")).toEqual([]);
    });

    it("resumes from the failed stage and shows the fresh progress", async () => {
      const api = progressApi();
      const user = renderProgressTab();
      await pipeline();
      const resume = within(rowOf("audit_translation")).getByRole("button", { name: "Resume" });
      expect(resume).toHaveAttribute("title", "Continues from the last checkpoint; finished work is kept.");
      const before = api.calls.filter((call) => call.path.startsWith("/api/jobs/demo/progress")).length;

      await user.click(resume);
      expect(await screen.findByRole("status")).toHaveTextContent("Resumed.");
      expect(api.posted("/api/jobs/demo/resume")).toEqual([{}]);
      await waitFor(() => expect(api.calls.filter((call) => call.path.startsWith("/api/jobs/demo/progress")).length).toBeGreaterThan(before));
    });

    it("shows why a resume was refused", async () => {
      progressApi({ "POST /api/jobs/demo/resume": apiError("another run is already active", 409) });
      const user = renderProgressTab();
      await pipeline();
      await user.click(within(rowOf("audit_translation")).getByRole("button", { name: "Resume" }));
      expect(await screen.findByRole("status")).toHaveTextContent("another run is already active");
      expect(within(rowOf("audit_translation")).getByRole("button", { name: "Resume" })).toBeEnabled();
    });

    const preview = (overrides: Record<string, unknown> = {}) => ({
      stage: "translate",
      status: "completed",
      stages: [
        { name: "translate", status: "completed", seconds: 4778 },
        { name: "audit_translation", status: "failed", seconds: null },
        { name: "compile", status: "pending", seconds: null },
        { name: "validate_epub", status: "pending", seconds: null },
      ],
      warnings: [{ code: "edits", message: "2 manual edits on the Text tab are kept and re-checked." }],
      previous_seconds: 5400,
      ...overrides,
    });

    it("warns what a rerun discards before doing it", async () => {
      const api = progressApi({ "GET /api/jobs/demo/rerun": preview() });
      const user = renderProgressTab();
      await pipeline();
      const rerun = within(rowOf("translate")).getByRole("button", { name: "Rerun from here" });
      expect(rerun).toHaveAttribute("title", "Resets this stage and every later stage, then runs them again.");

      await user.click(rerun);
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Rerun from Translate?");
      expect(api.requested("/api/jobs/demo/rerun?stage=translate")).toBe(true);
      expect(dialog).toHaveTextContent("These stages are reset and run again; their finished work is discarded:");
      expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Translate · took ~1 h 20 min", "Audit translation"]);
      expect(dialog).toHaveTextContent("The run then continues through the 2 later stages that have not run yet.");
      expect(dialog).toHaveTextContent("These stages took about 1 h 30 min so far.");
      expect(dialog).toHaveTextContent("2 manual edits on the Text tab are kept and re-checked.");
      expect(dialog).not.toHaveTextContent("Resume would keep");
      expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus(); // the safe choice is the default

      await user.click(within(dialog).getByRole("button", { name: "Rerun" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Rerunning from Translate.");
      expect(api.posted("/api/jobs/demo/rerun")).toEqual([{ stage: "translate" }]);
    });

    it("does nothing when the rerun is cancelled", async () => {
      const api = progressApi({ "GET /api/jobs/demo/rerun": preview() });
      const user = renderProgressTab();
      await pipeline();
      await user.click(within(rowOf("translate")).getByRole("button", { name: "Rerun from here" }));
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
    });

    it("reminds the user that a stopped stage could be resumed instead", async () => {
      progressApi({
        "GET /api/jobs/demo/rerun": preview({
          stage: "audit_translation", status: "failed", previous_seconds: null, warnings: [],
          stages: [{ name: "audit_translation", status: "failed", seconds: null }, { name: "compile", status: "pending", seconds: null }],
        }),
      });
      const user = renderProgressTab();
      await pipeline();
      await user.click(within(rowOf("audit_translation")).getByRole("button", { name: "Rerun" }));

      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveTextContent("Resume would keep this stage's finished work; a rerun starts it over.");
      expect(dialog).toHaveTextContent("This stage is reset and runs again; its finished work is discarded:");
      expect(dialog).toHaveTextContent("The run then continues through the 1 later stage that has not run yet.");
      expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["Audit translation"]);
      expect(dialog).not.toHaveTextContent("These stages took about");
    });

    it("shows why a rerun cannot be previewed, and asks nothing", async () => {
      const api = progressApi({ "GET /api/jobs/demo/rerun": apiError("the job is running", 409) });
      const user = renderProgressTab();
      await pipeline();
      await user.click(within(rowOf("translate")).getByRole("button", { name: "Rerun from here" }));
      expect(await screen.findByRole("status")).toHaveTextContent("the job is running");
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
    });

    it("blocks the actions while the job is running, and says why", async () => {
      const stages = [stage("decompile", "completed"), stage("translate", "running")];
      progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages }),
        "GET /api/jobs/demo/progress": snapshot({ status: running(stages) }),
      });
      renderProgressTab();
      await pipeline();
      const rerun = within(rowOf("decompile")).getByRole("button", { name: "Rerun from here" });
      expect(rerun).toBeDisabled();
      expect(rerun).toHaveAttribute("title", "The job is running; pause or stop it first.");
    });

    it("blocks the actions while a dashboard-launched command is still running", async () => {
      progressApi({
        "GET /api/jobs/demo/progress": snapshot({ process: { label: "resume", running: true, exit_code: null, outcome: "running", started: "10:00", output_tail: "" } }),
      });
      renderProgressTab();
      await pipeline();
      expect(within(rowOf("audit_translation")).getByRole("button", { name: "Resume" })).toBeDisabled();
      expect(within(rowOf("audit_translation")).getByRole("button", { name: "Rerun" })).toBeDisabled();
    });
  });

  describe("session log", () => {
    const LINES = [
      "[10:00:01] [translate] [running] chunk 4",
      "[10:00:02] [llm] streaming — 120 tokens",
      "[10:00:03] [translate] [segment] D0000-S000004 ok",
      "[10:00:04] translate failed: timeout",
    ];
    const logApi = (lines = LINES) => progressApi({ "GET /api/jobs/demo/progress": snapshot({}, { lines, line_count: lines.length }) });
    const shownLines = () => [...logBox().children].map((line) => line.textContent);

    it("names the log file and hides heartbeats and per-segment results by default", async () => {
      logApi();
      renderProgressTab();
      expect(await screen.findByRole("heading", { name: /Session log/ })).toHaveTextContent("session-1.log");
      expect(shownLines()).toEqual([LINES[0], LINES[3]]);
      expect(screen.getByRole("checkbox", { name: "hide streaming heartbeats" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "hide per-segment results" })).toBeChecked();
      expect(screen.getByRole("checkbox", { name: "follow" })).toBeChecked();
    });

    it("shows heartbeats and per-segment results when asked", async () => {
      logApi();
      const user = renderProgressTab();
      await screen.findByRole("heading", { name: /Session log/ });

      await user.click(screen.getByRole("checkbox", { name: "hide streaming heartbeats" }));
      expect(shownLines()).toEqual([LINES[0], LINES[1], LINES[3]]);

      await user.click(screen.getByRole("checkbox", { name: "hide per-segment results" }));
      expect(shownLines()).toEqual(LINES);

      await user.click(screen.getByRole("checkbox", { name: "hide streaming heartbeats" }));
      expect(shownLines()).toEqual([LINES[0], LINES[2], LINES[3]]);
    });

    it("says when there is nothing to show", async () => {
      logApi([LINES[1]]); // only a heartbeat, which is hidden
      const user = renderProgressTab();
      await screen.findByRole("heading", { name: /Session log/ });
      expect(within(logBox()).getByText("No log lines yet.")).toBeInTheDocument();

      await user.click(screen.getByRole("checkbox", { name: "hide streaming heartbeats" }));
      expect(shownLines()).toEqual([LINES[1]]);
    });

    it("can stop following the log", async () => {
      logApi();
      const user = renderProgressTab();
      await screen.findByRole("heading", { name: /Session log/ });
      const follow = screen.getByRole("checkbox", { name: "follow" });
      await user.click(follow);
      expect(follow).not.toBeChecked();
    });
  });

  describe("staying up to date", () => {
    const requests = (api: ReturnType<typeof progressApi>) => api.calls.filter((call) => call.path.startsWith("/api/jobs/demo/progress")).map((call) => call.path);

    it("adds new log lines below the ones already shown", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let batch = 0;
      const api = progressApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, stages: running().stages }),
        "GET /api/jobs/demo/progress": () => {
          batch += 1;
          return snapshot({ status: running() }, { lines: [`line ${batch}`], line_count: batch });
        },
      });
      renderProgressTab();
      expect(await screen.findByText("line 1")).toBeInTheDocument();
      expect(requests(api)).toEqual(["/api/jobs/demo/progress?after=0&log="]);

      await act(async () => { await vi.advanceTimersByTimeAsync(2000); }); // a live run polls every two seconds
      expect(await screen.findByText("line 2")).toBeInTheDocument();
      expect(screen.getByText("line 1")).toBeInTheDocument();
      expect(requests(api)[1]).toBe("/api/jobs/demo/progress?after=1&log=session-1.log");
    });

    it("starts the log over when a new session begins", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let session = 1;
      progressApi({
        "GET /api/jobs/demo/progress": () => snapshot({}, { log: `session-${session}.log`, lines: [`from session ${session}`], line_count: 1 }),
      });
      renderProgressTab();
      expect(await screen.findByText("from session 1")).toBeInTheDocument();

      session = 2;
      await act(async () => { await vi.advanceTimersByTimeAsync(8000); }); // an idle job polls every eight seconds
      expect(await screen.findByText("from session 2")).toBeInTheDocument();
      expect(screen.queryByText("from session 1")).not.toBeInTheDocument();
    });

    it("stops asking once the user leaves the tab, even with a request still on its way", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      const gate = deferred();
      let held = false;
      const api = progressApi({
        "GET /api/jobs/demo/progress": () => {
          if (held) return snapshot();
          held = true;
          return gate.promise;
        },
        "GET /api/setup": { configs: [], config_dir: "configs", runs: "runs", template: "", jobs: [] },
      });
      const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
      window.history.pushState({}, "", "/jobs/demo/progress");
      render(<App />);
      await screen.findByText("Loading…");

      await user.click(screen.getByRole("link", { name: "Ollama Translator" }));
      expect(await screen.findByText(/No jobs yet/)).toBeInTheDocument();
      await act(async () => { gate.resolve(snapshot()); await vi.advanceTimersByTimeAsync(30_000); });
      expect(requests(api)).toHaveLength(1);
      expect(api.count("/api/jobs/demo/info")).toBeLessThanOrEqual(1);
    });

    it("keeps what it has on screen while the server is down, says so, and carries on when it is back", async () => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
      let down = false;
      progressApi({ "GET /api/jobs/demo/progress": () => (down ? apiError("server restarting", 503) : snapshot()) });
      renderProgressTab();
      await pipeline();

      down = true;
      await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
      expect(await screen.findByText("server restarting")).toBeInTheDocument();
      expect(screen.getByRole("heading", { name: "Pipeline" })).toBeInTheDocument();

      down = false;
      await act(async () => { await vi.advanceTimersByTimeAsync(8000); });
      await waitFor(() => expect(screen.queryByText("server restarting")).not.toBeInTheDocument());
    });
  });
});

// -- journeys -------------------------------------------------------------------
// README: "The Progress page follows the job live"; "A failed or paused stage offers
// Resume (finished work is kept) and Rerun; a completed one offers Rerun from here".

describe("Progress tab: following a book through the pipeline", () => {
  /** A pretend server for one job: tests change `state` the way the pipeline would. */
  function book(initial: { overall: string; stages: ReturnType<typeof stage>[]; running?: boolean }, overrides: Record<string, unknown> = {}) {
    const state = { ...initial, lines: [] as string[], lastLlm: "" };
    const status = () => ({ job_id: "demo", overall: state.overall, stages: state.stages, configuration: { source_path: "" } });
    const api = progressApi({
      "GET /api/jobs/demo/info": () => jobInfo({ overall: state.overall, running: !!state.running, can_stop: !!state.running, stages: state.stages }),
      "GET /api/jobs/demo/progress": (_: unknown, url: URL) => {
        const after = Number(url.searchParams.get("after"));
        return snapshot(
          { status: status(), source: "the-sign-of-the-four.epub" },
          { log: "session-0002.log", lines: state.lines.slice(after), line_count: state.lines.length, last_llm: state.lastLlm, stages: { translate: activity({ counter: [38, 120] }) } },
        );
      },
      "GET /api/jobs/demo/estimate": () => estimate({
        complete: state.overall === "complete",
        remaining_seconds: 2700,
        current: state.running ? { stage: "translate", done: 38, total: 120, seconds_per_unit: 28, remaining_seconds: 2300, basis: "28 s per unit so far", running: true } : null,
      }),
      "POST /api/jobs/demo/resume": () => {
        state.overall = "running";
        state.running = true;
        state.stages = state.stages.map((s) => (s.status === "failed" ? { ...s, status: "running", message: "" } : s));
        return { ok: true };
      },
      ...overrides,
    });
    return { api, state };
  }
  const done = (name: string) => stage(name, "completed", { attempts: 1 });

  it("a translator watches The Sign of the Four translate: the stage, the call in flight, the time left, and new log lines", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const { state } = book({
      overall: "running", running: true,
      stages: [done("decompile"), done("approve_glossary"), stage("translate", "running", { attempts: 1, message: "chapter 4 of 12" }), stage("compile", "pending")],
    });
    state.lines = ["[09:12:01] [translate] [running] chapter 4 of 12"];
    state.lastLlm = "translate · chapter 4 · “The Story of the Bald-Headed Man”";
    renderProgressTab();

    const summary = (await screen.findByText("2 of 4 stages complete")).closest("section") as HTMLElement;
    expect(summary).toHaveTextContent("the-sign-of-the-four.epub");
    expect(within(summary).getByText("Translate", { selector: ".big" })).toBeInTheDocument(); // the stage it is at, in large type
    expect(within(summary).getByText("chapter 4 of 12")).toBeInTheDocument();
    expect(summary).toHaveTextContent("≈ 45 min left");
    expect((screen.getByRole("heading", { name: "Now" }).closest("section") as HTMLElement)).toHaveTextContent("The Story of the Bald-Headed Man");
    expect(cells("translate")[2]).toHaveTextContent("unit 38/120");
    expect(screen.getByText("[09:12:01] [translate] [running] chapter 4 of 12")).toBeInTheDocument();

    // Two seconds later the next chapter has started.
    state.lines.push("[09:12:03] [translate] [running] chapter 5 of 12");
    await act(async () => { await vi.advanceTimersByTimeAsync(2000); });
    expect(await screen.findByText("[09:12:03] [translate] [running] chapter 5 of 12")).toBeInTheDocument();
    expect(screen.getByText("[09:12:01] [translate] [running] chapter 4 of 12")).toBeInTheDocument();
  });

  it("the audit failed overnight: the translator reads why, resumes it, and sees the run pick up again", async () => {
    const { api } = book({
      overall: "failed",
      stages: [done("decompile"), done("translate"), stage("audit_translation", "failed", { attempts: 1, message: "Ollama did not answer within 600 s" }), stage("compile", "pending")],
    });
    const user = renderProgressTab();

    expect(await screen.findByText("2 of 4 stages complete")).toBeInTheDocument();
    expect(within(rowOf("audit_translation")).getByText("Ollama did not answer within 600 s")).toBeInTheDocument();
    expect(cells("audit_translation")[0]).toHaveTextContent("✕");

    await user.click(within(rowOf("audit_translation")).getByRole("button", { name: "Resume" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Resumed.");
    expect(api.posted("/api/jobs/demo/resume")).toEqual([{}]);

    // The page now shows a running job: the failed mark is gone and the row's actions are locked.
    await waitFor(() => expect(cells("audit_translation")[0]).toHaveTextContent("◐"));
    expect(within(rowOf("translate")).getByRole("button", { name: "Rerun from here" })).toBeDisabled();
    expect(await screen.findByRole("button", { name: "❚❚ Pause" })).toBeInTheDocument();
  });

  it("a translator changes the style and reruns from Translate, after reading what will be redone", async () => {
    const preview = {
      stage: "translate", status: "completed", previous_seconds: 9000,
      stages: [
        { name: "translate", status: "completed", seconds: 7200 },
        { name: "audit_translation", status: "completed", seconds: 1500 },
        { name: "compile", status: "completed", seconds: 20 },
      ],
      warnings: [{ code: "edits", message: "3 edits made on the Text tab are kept; segments that change under them become conflicts." }],
    };
    const { api } = book(
      { overall: "complete", stages: [done("decompile"), done("translate"), done("audit_translation"), done("compile")] },
      { "GET /api/jobs/demo/rerun": preview },
    );
    const user = renderProgressTab();
    expect(await screen.findByText("All stages complete")).toBeInTheDocument();

    await user.click(within(rowOf("translate")).getByRole("button", { name: "Rerun from here" }));
    const dialog = await screen.findByRole("alertdialog");
    expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual([
      "Translate · took ~2 h", "Audit translation · took ~25 min", "Compile EPUB · took ~20 s",
    ]);
    expect(dialog).toHaveTextContent("These stages took about 2 h 30 min so far.");
    expect(dialog).toHaveTextContent("3 edits made on the Text tab are kept");

    // Two and a half hours of work: the translator thinks again, then goes ahead.
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
    await user.click(within(rowOf("translate")).getByRole("button", { name: "Rerun from here" }));
    await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Rerun" }));
    expect(await screen.findByRole("status")).toHaveTextContent("Rerunning from Translate.");
    expect(api.posted("/api/jobs/demo/rerun")).toEqual([{ stage: "translate" }]);
  });

  it("the run stops for the glossary: the page says who it is waiting for and takes the translator there", async () => {
    book({
      overall: "paused",
      stages: [done("decompile"), done("extract_glossary"), done("resolve_glossary"), stage("approve_glossary", "paused", { attempts: 1, message: "human glossary review required", updated_at: todayAt(8, 30) }), stage("translate", "pending")],
    });
    const user = renderProgressTab();
    const summary = (await screen.findByText("3 of 5 stages complete")).closest("section") as HTMLElement;
    expect(within(summary).getByText("Approve glossary")).toBeInTheDocument();
    expect(within(summary).getByText("human glossary review required")).toBeInTheDocument();
    expect(cells("approve_glossary")[8]).toHaveTextContent("waiting for review 08:30");
    expect(within(rowOf("approve_glossary")).queryByRole("button", { name: /Resume|Rerun/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Review glossary" }));
    expect(window.location.pathname).toBe("/jobs/demo/glossary");
  });
});
