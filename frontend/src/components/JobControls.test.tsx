import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { jobInfo, renderInJob, stage } from "../test/job";
import { JobControls } from "./JobControls";

const INFO = "/api/jobs/demo/info";
const draft = (overrides: Record<string, unknown> = {}) => jobInfo({ kind: "draft", overall: "draft", validated: true, ...overrides });
const running = (overrides: Record<string, unknown> = {}) => jobInfo({ overall: "running", running: true, can_stop: true, ...overrides });
const stopped = (overrides: Record<string, unknown> = {}) =>
  jobInfo({ overall: "paused", stages: [stage("translate", "paused")], ...overrides });

function controlsApi(info: unknown, routes: Record<string, unknown> = {}) {
  return mockApi({ "GET /api/jobs/demo/info": info, ...routes });
}

/** Render the controls and wait for the job to load (the status chip appears). */
async function renderControls(status: string) {
  const user = userEvent.setup();
  renderInJob(<JobControls />);
  await screen.findByText(status);
  return user;
}

const confirmIn = async (user: ReturnType<typeof userEvent.setup>, label: string) =>
  user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: label }));

describe("The job's buttons in the header", () => {
  it("are not shown until the job has loaded", () => {
    const gate = deferred();
    controlsApi(() => gate.promise);
    renderInJob(<JobControls />);
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.queryByText("complete")).not.toBeInTheDocument();
  });

  describe("a draft", () => {
    it("can be started once validated, then moves to the Progress tab", async () => {
      const api = controlsApi(draft(), { "POST /api/jobs/demo/start": { started: true } });
      const user = await renderControls("draft");
      const start = screen.getByRole("button", { name: "Start translation" });
      expect(start).toBeEnabled();
      expect(start.parentElement).toHaveAttribute("title", "Starts the pipeline with the validated configuration.");

      await user.click(start);
      expect(await screen.findByText("at /jobs/demo/progress")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Translation started.");
      expect(api.posted("/api/jobs/demo/start")).toEqual([{}]);
      expect(api.count(INFO)).toBe(2); // the job is reloaded before moving on
    });

    it("cannot be started before it is validated, and says why", async () => {
      controlsApi(draft({ validated: false }));
      await renderControls("draft");
      const start = screen.getByRole("button", { name: "Start translation" });
      expect(start).toBeDisabled();
      expect(start.parentElement).toHaveAttribute("title", "Validate the configuration on the Config tab first.");
    });

    it("is discarded only after confirming, then returns to the job list", async () => {
      const api = controlsApi(draft(), { "POST /api/jobs/demo/discard": { discarded: true } });
      const user = await renderControls("draft");

      await user.click(screen.getByRole("button", { name: "Discard" }));
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Discard this job?");
      expect(dialog).toHaveTextContent("The draft job is removed. Its config file is kept.");
      expect(api.posted("/api/jobs/demo/discard")).toEqual([]);

      await confirmIn(user, "Discard");
      expect(await screen.findByText("at /")).toBeInTheDocument();
      expect(screen.getByRole("status")).toHaveTextContent("Draft discarded.");
      expect(api.posted("/api/jobs/demo/discard")).toEqual([{}]);
    });

    it("is kept when the discard is cancelled", async () => {
      const api = controlsApi(draft(), { "POST /api/jobs/demo/discard": { discarded: true } });
      const user = await renderControls("draft");
      await user.click(screen.getByRole("button", { name: "Discard" }));
      await confirmIn(user, "Cancel");

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/discard")).toEqual([]);
      expect(screen.getByRole("button", { name: "Discard" })).toBeEnabled();
    });

    it("offers neither Pause nor Resume, nor Start and Discard while it is starting", async () => {
      controlsApi(draft({ overall: "starting", running: true, can_stop: true }));
      await renderControls("starting");
      expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["■ Stop"]);
    });
  });

  describe("a running job", () => {
    it("asks the run to pause after the current call", async () => {
      const api = controlsApi(running(), { "POST /api/jobs/demo/pause": { ok: true } });
      const user = await renderControls("running");
      const pause = screen.getByRole("button", { name: "❚❚ Pause" });
      expect(pause.parentElement).toHaveAttribute("title", expect.stringContaining("Lets the current LLM call finish, then pauses."));

      await user.click(pause);
      expect(await screen.findByRole("status")).toHaveTextContent("Pause requested: the run stops after the current LLM call.");
      expect(api.posted("/api/jobs/demo/pause")).toEqual([{}]);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(); // no confirmation: nothing is lost
    });

    it("shows that a pause is on its way and cannot be asked for twice", async () => {
      controlsApi(running({ pause_requested: true }));
      await renderControls("pausing");
      const pause = screen.getByRole("button", { name: "Pausing…" });
      expect(pause).toBeDisabled();
      expect(pause.parentElement).toHaveAttribute("title", "Waiting for the current LLM call to finish.");
      expect(screen.queryByText("running")).not.toBeInTheDocument();
    });

    it("stops only after confirming that the call in progress is lost", async () => {
      const api = controlsApi(running(), { "POST /api/jobs/demo/stop": { ok: true } });
      const user = await renderControls("running");
      const stop = screen.getByRole("button", { name: "■ Stop" });
      expect(stop.parentElement).toHaveAttribute("title", "Stops immediately. The LLM call in progress is lost and redone on resume.");

      await user.click(stop);
      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Stop the run now?");
      expect(dialog).toHaveTextContent("The LLM call in progress is discarded and redone when you resume.");

      await confirmIn(user, "Stop");
      expect(await screen.findByRole("status")).toHaveTextContent("Stopped.");
      expect(api.posted("/api/jobs/demo/stop")).toEqual([{}]);
    });

    it("keeps running when the stop is cancelled", async () => {
      const api = controlsApi(running(), { "POST /api/jobs/demo/stop": { ok: true } });
      const user = await renderControls("running");
      await user.click(screen.getByRole("button", { name: "■ Stop" }));
      await confirmIn(user, "Cancel");
      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(api.posted("/api/jobs/demo/stop")).toEqual([]);
    });

    it("cannot stop a run that was not started from the dashboard", async () => {
      controlsApi(running({ can_stop: false }));
      await renderControls("running");
      const stop = screen.getByRole("button", { name: "■ Stop" });
      expect(stop).toBeDisabled();
      expect(stop.parentElement).toHaveAttribute("title", "Only runs started from this dashboard can be stopped here; use Pause.");
      expect(screen.getByRole("button", { name: "❚❚ Pause" })).toBeEnabled();
    });

    it("offers no Resume", async () => {
      controlsApi(running());
      await renderControls("running");
      expect(screen.queryByRole("button", { name: "▶ Resume" })).not.toBeInTheDocument();
    });
  });

  describe("a stopped job", () => {
    it("resumes from the last checkpoint", async () => {
      const api = controlsApi(stopped(), { "POST /api/jobs/demo/resume": { ok: true } });
      const user = await renderControls("paused");
      const resume = screen.getByRole("button", { name: "▶ Resume" });
      expect(resume.parentElement).toHaveAttribute("title", "Continues from the last checkpoint.");
      expect(screen.queryByRole("button", { name: "■ Stop" })).not.toBeInTheDocument();

      await user.click(resume);
      expect(await screen.findByRole("status")).toHaveTextContent("Resumed.");
      expect(api.posted("/api/jobs/demo/resume")).toEqual([{}]);
      expect(api.count(INFO)).toBe(2);
    });

    it.each([
      ["the glossary gate", "approve_glossary"],
      ["the final review", "compile"],
    ])("has no Resume while it waits at %s", async (_gate, name) => {
      controlsApi(stopped({ stages: [stage(name, "paused")] }));
      await renderControls("paused");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("has no Resume once complete, and offers the book when it can be downloaded", async () => {
      controlsApi(jobInfo({ downloadable: true }));
      await renderControls("complete");
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
      const download = screen.getByRole("link", { name: "⤓ Download" });
      expect(download).toHaveAttribute("href", "/api/jobs/demo/output");
      expect(download).toHaveAttribute("download");
    });

    it("lets the reader take the book as a Word document instead of an EPUB", async () => {
      controlsApi(jobInfo({ downloadable: true }));
      await renderControls("complete");
      const format = screen.getByRole("combobox", { name: "Download format" });
      expect(format).toHaveValue("epub");
      expect(within(format).getAllByRole("option").map((option) => option.textContent)).toEqual([
        "EPUB", "Word (.docx)", "HTML", "Markdown", "Plain text",
      ]);
      await userEvent.selectOptions(format, "Word (.docx)");
      expect(screen.getByRole("link", { name: "⤓ Download" })).toHaveAttribute("href", "/api/jobs/demo/output?format=docx");
    });

    it("has no download link before there is a book", async () => {
      controlsApi(stopped());
      await renderControls("paused");
      expect(screen.queryByRole("link", { name: "⤓ Download" })).not.toBeInTheDocument();
    });
  });

  describe("when the server refuses", () => {
    it("shows the reason, stays on the page, and leaves the controls usable", async () => {
      const api = controlsApi(draft(), { "POST /api/jobs/demo/start": apiError("Ollama is not reachable at http://localhost:11434") });
      const user = await renderControls("draft");
      await user.click(screen.getByRole("button", { name: "Start translation" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Ollama is not reachable at http://localhost:11434");
      expect(screen.queryByText("at /jobs/demo/progress")).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Start translation" })).toBeEnabled();
      expect(api.count(INFO)).toBe(1);
    });

    it("cannot be clicked twice while the first click is still being carried out", async () => {
      const gate = deferred();
      controlsApi(draft(), { "POST /api/jobs/demo/start": () => gate.promise });
      const user = await renderControls("draft");
      await user.click(screen.getByRole("button", { name: "Start translation" }));

      expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
      expect(screen.getByRole("button", { name: "Discard" })).toBeDisabled();
      gate.resolve({ started: true });
      expect(await screen.findByText("at /jobs/demo/progress")).toBeInTheDocument();
    });
  });
});
