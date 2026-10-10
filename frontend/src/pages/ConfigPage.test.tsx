import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { App } from "../App";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { jobInfo } from "../test/job";

// -- fixtures -----------------------------------------------------------------

const YAML = "ollama:\n  model: qwen3:14b\n";
const SOURCE = "D:\\books\\alice.epub";

const field = (path: string, type: string, value: unknown) => ({ path, key: path.split(".").at(-1), type, default: value, nullable: false });
const SCHEMA = {
  sections: [
    { key: "translation", title: "Translation", fields: [field("translation.direction", "string", "en-zh"), field("translation.style", "string", "literary")] },
    { key: "ollama", title: "Ollama", fields: [field("ollama.model", "string", "qwen3:14b")] },
  ],
};

const BOOK = {
  name: "alice.epub", path: SOURCE, size: 2048, format: "epub",
  title: "Alice's Adventures in Wonderland", authors: ["Lewis Carroll"], language: "en", has_cover: true,
};

const draft = (overrides: Record<string, unknown> = {}) =>
  jobInfo({ kind: "draft", overall: "draft", validated: false, source_path: SOURCE, ...overrides });

const check = (overrides: Record<string, unknown> = {}) => ({
  ok: true,
  validated: true,
  problems: [],
  job_id: "demo",
  models: [{ role: "primary", model: "qwen3:14b", installed: true }],
  summary: { direction: "en-zh", style: "literary", glossary_review: "human", semantic_audit: true, reprose: false, final_review_required: false },
  stages: ["decompile", "translate"],
  ...overrides,
});

function configApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/jobs/demo/info": draft(),
    "GET /api/jobs/demo/config": { editable: true, name: "demo.yaml", text: YAML, validated: false },
    "POST /api/jobs/demo/validate": check(),
    "GET /api/book": BOOK,
    "GET /api/config/schema": SCHEMA,
    "POST /api/config/parse": { values: { ollama: { model: "qwen3:14b" } }, syntax_error: null },
    "POST /api/config/check": { errors: [] },
    "POST /api/config/dump": { text: YAML },
    "GET /api/models": { installed: ["qwen3:14b"] },
    "GET /api/languages": { languages: [], support: null, error: "" },
    ...overrides,
  });
}

function renderConfigTab() {
  window.history.pushState({}, "", "/jobs/demo/config");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

/** Change the config through the YAML tab, the simplest edit there is. */
async function editYaml(user: ReturnType<typeof userEvent.setup>, extra = "# tuned by hand\n") {
  await user.click(await screen.findByRole("tab", { name: "YAML" }));
  await user.type(screen.getByRole("textbox"), extra);
}

const validateCard = () => screen.getByRole("heading", { name: "Validate" }).closest("section") as HTMLElement;

// -- tests --------------------------------------------------------------------

describe("Config tab", () => {
  describe("a draft job", () => {
    it("shows the book, the config file, and that it is not validated yet", async () => {
      const api = configApi();
      renderConfigTab();
      expect(screen.getByText("Loading…")).toBeInTheDocument();

      expect(await screen.findByRole("heading", { name: "Configuration" })).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
      expect(screen.getByText(BOOK.title)).toBeInTheDocument();
      expect(screen.getByText("Config", { selector: "dt" }).nextElementSibling).toHaveTextContent("demo.yaml");
      expect(api.requested(`/api/book?path=${encodeURIComponent(SOURCE)}`)).toBe(true);

      const card = within(validateCard());
      expect(card.getByText(/Not validated yet\./)).toHaveTextContent("Not validated yet. Validate to enable Start translation.");
      expect(card.getByRole("button", { name: "Validate" })).toBeEnabled();
      expect(card.getByText(/checks every setting, dry-runs the job/)).toHaveTextContent("Saves demo.yaml, checks every setting");
      expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
    });

    it("shows where the book file should be when the file itself cannot be read", async () => {
      configApi({ "GET /api/book": apiError("file not found", 404) });
      renderConfigTab();
      await screen.findByRole("heading", { name: "Configuration" });
      expect(screen.getByText("Source").parentElement).toHaveTextContent(`Source ${SOURCE}`);
      expect(screen.getByText("Config file").parentElement).toHaveTextContent("Config file demo.yaml");
      expect(screen.queryByText(BOOK.title)).not.toBeInTheDocument();
    });

    it("shows just the book's file name for a job that does not know where the file is", async () => {
      const api = configApi({ "GET /api/jobs/demo/info": draft({ source_path: undefined }) });
      renderConfigTab();
      await screen.findByRole("heading", { name: "Configuration" });
      expect(screen.getByText("Source").parentElement).toHaveTextContent("Source book.epub");
      expect(api.calls.some((call) => call.path.startsWith("/api/book"))).toBe(false);
    });

    it("warns about unsaved changes as soon as the text differs from the saved file", async () => {
      configApi();
      const user = renderConfigTab();
      await editYaml(user);

      const card = within(validateCard());
      expect(card.getByText(/Unsaved changes\./)).toHaveTextContent("Validate saves them; until then the job would start with the last validated file.");
      expect(card.getByRole("button", { name: "Save & validate" })).toBeEnabled();
      expect(card.queryByText(/Not validated yet/)).not.toBeInTheDocument();
    });

    it("saves and validates the edited text, shows the result, and unlocks Start", async () => {
      let validated = false;
      const api = configApi({
        "GET /api/jobs/demo/info": () => draft({ validated }),
        "POST /api/jobs/demo/validate": () => { validated = true; return check(); },
      });
      const user = renderConfigTab();
      await editYaml(user);
      await user.click(screen.getByRole("button", { name: "Save & validate" }));

      const card = within(validateCard());
      expect(await card.findByText(/at the top is ready./)).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/validate")).toEqual([{ text: `${YAML}# tuned by hand\n` }]);
      // The check's own summary: verdict, settings, models, pipeline.
      expect(card.getByText(/at the top to run/)).toHaveTextContent("run demo.");
      expect(card.getByText("glossary review: human")).toBeInTheDocument();
      expect(card.getByRole("cell", { name: "qwen3:14b" })).toBeInTheDocument();
      expect(card.getByText("Decompile source")).toBeInTheDocument();
      expect(card.getByRole("button", { name: "Validate" })).toBeEnabled(); // saved: no longer "Save & validate"
      expect(screen.getByRole("button", { name: "Start translation" })).toBeEnabled();
    });

    it("asks for validation again after a further edit", async () => {
      let validated = false;
      configApi({
        "GET /api/jobs/demo/info": () => draft({ validated }),
        "POST /api/jobs/demo/validate": () => { validated = true; return check(); },
      });
      const user = renderConfigTab();
      await editYaml(user);
      await user.click(screen.getByRole("button", { name: "Save & validate" }));
      await within(validateCard()).findByText(/at the top is ready./);

      await user.type(screen.getByRole("textbox"), "x");
      expect(within(validateCard()).getByText(/Unsaved changes\./)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save & validate" })).toBeInTheDocument();
    });

    it("lists what is wrong when the config is saved but not ready", async () => {
      configApi({ "POST /api/jobs/demo/validate": check({ ok: false, validated: false, problems: ["model qwen3:14b is not installed"] }) });
      const user = renderConfigTab();
      await user.click(await screen.findByRole("button", { name: "Validate" }));

      const card = within(validateCard());
      expect(await card.findByText(/Not ready yet:/)).toHaveTextContent("model qwen3:14b is not installed");
      expect(card.getByText(/Not validated yet\./)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();
    });

    it("says the file was not saved when the server refuses it, and keeps the edit", async () => {
      configApi({ "POST /api/jobs/demo/validate": apiError("translation.style: unknown style 'fancy'", 422) });
      const user = renderConfigTab();
      await editYaml(user, "x");
      await user.click(screen.getByRole("button", { name: "Save & validate" }));

      const toast = await screen.findByRole("status");
      expect(toast).toHaveTextContent("Not saved:");
      expect(toast).toHaveTextContent("translation.style: unknown style 'fancy'");
      expect(within(validateCard()).getByText(/Unsaved changes\./)).toBeInTheDocument();
      expect(within(validateCard()).queryByText(/Not ready yet|at the top to run/)).not.toBeInTheDocument();
      expect(screen.getByRole("textbox")).toHaveValue(`${YAML}x`);
      expect(screen.getByRole("button", { name: "Save & validate" })).toBeEnabled();
    });

    it("drops an earlier result when a later validation is refused", async () => {
      let refuse = false;
      configApi({ "POST /api/jobs/demo/validate": () => (refuse ? apiError("disk full", 500) : check()) });
      const user = renderConfigTab();
      await user.click(await screen.findByRole("button", { name: "Validate" }));
      await within(validateCard()).findByText(/at the top to run/);

      refuse = true;
      await user.click(screen.getByRole("button", { name: "Validate" }));
      expect(await screen.findByRole("status")).toHaveTextContent("disk full");
      expect(within(validateCard()).queryByText(/at the top to run/)).not.toBeInTheDocument();
    });

    it("shows that validation is running and cannot be started twice", async () => {
      const gate = deferred();
      const api = configApi({ "POST /api/jobs/demo/validate": () => gate.promise });
      const user = renderConfigTab();
      await user.click(await screen.findByRole("button", { name: "Validate" }));

      const button = screen.getByRole("button", { name: "Validating…" });
      expect(button).toBeDisabled();
      await user.click(button);
      gate.resolve(check());
      expect(await within(validateCard()).findByText(/at the top to run/)).toBeInTheDocument();
      expect(api.posted("/api/jobs/demo/validate")).toHaveLength(1);
    });

    it("warns that the form rewrites a file that has comments", async () => {
      configApi({ "GET /api/jobs/demo/config": { editable: true, name: "demo.yaml", text: `# house rules\n${YAML}`, validated: false } });
      renderConfigTab();
      expect(await screen.findByText("Changing an option rewrites the YAML from the form; comments in the file are not kept.")).toBeInTheDocument();
    });

    it("has no comment warning for a file without comments", async () => {
      configApi();
      renderConfigTab();
      await screen.findByRole("heading", { name: "Configuration" });
      expect(screen.queryByText(/comments in the file are not kept/)).not.toBeInTheDocument();
    });

    it("shows why the last start failed", async () => {
      configApi({
        "GET /api/jobs/demo/info": draft({
          process: { label: "start", running: false, exit_code: 2, outcome: "failed", started: "10:00", output_tail: "ValueError: source file is not an EPUB" },
        }),
      });
      renderConfigTab();
      expect(await screen.findByText(/The last start failed before the job workspace was created:/)).toHaveTextContent("ValueError: source file is not an EPUB");
    });
  });

  describe("a started job", () => {
    const startedApi = () =>
      configApi({
        "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, source_path: SOURCE }),
        "GET /api/jobs/demo/config": { editable: false, name: "demo.yaml", text: YAML, validated: true },
      });

    it("shows the captured config read-only, with nothing to validate", async () => {
      startedApi();
      const user = renderConfigTab();
      expect(await screen.findByRole("heading", { name: "Configuration used by this run" })).toBeInTheDocument();
      // Running: it cannot be unlocked, and the page says what to do first.
      expect(screen.getByText("The job is running. Pause or stop it to change its configuration.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Unlock to edit" })).toBeDisabled();
      expect(screen.queryByRole("heading", { name: "Validate" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Validate/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole("tab", { name: "YAML" }));
      const yaml = screen.getByRole("textbox");
      expect(yaml).toHaveValue(YAML);
      expect(yaml).toHaveAttribute("readonly");
    });

    describe("that is not running", () => {
      const NEXT = "ollama:\n  model: qwen3:14b\noutput:\n  format: docx\n";
      const change = (overrides: Record<string, unknown> = {}) => ({
        path: "output.format", before: "source", after: "docx", stage: "compile", finished: true, locked: "", ...overrides,
      });
      const pausedApi = (overrides: Record<string, unknown> = {}) =>
        configApi({
          "GET /api/jobs/demo/info": jobInfo({ overall: "paused", source_path: SOURCE }),
          "GET /api/jobs/demo/config": {
            editable: false, name: "demo.yaml", text: YAML, validated: true, unlockable: true,
            locked: { "translation.direction": "direction", "paths.": "paths" }, changes: [],
          },
          "POST /api/jobs/demo/config/preview": { errors: [], changes: [change()], locked: [], rerun_stages: ["compile"] },
          "POST /api/jobs/demo/config": { saved: true, rerun: { started: true } },
          "GET /api/jobs/demo/rerun": {
            stage: "compile", status: "completed", previous_seconds: 4, warnings: [{ code: "compiled_epub", message: "replaced" }],
            stages: [{ name: "compile", status: "completed", seconds: 3 }, { name: "validate_epub", status: "completed", seconds: 1 }],
          },
          "POST /api/jobs/demo/rerun": { started: true },
          ...overrides,
        });

      /** Unlock, then write `yaml` as the config's text on the YAML tab. */
      async function unlockAndWrite(user: ReturnType<typeof userEvent.setup>, yaml: string) {
        await user.click(await screen.findByRole("button", { name: "Unlock to edit" }));
        await user.click(screen.getByRole("tab", { name: "YAML" }));
        const editor = screen.getByRole("textbox");
        await user.clear(editor);
        await user.click(editor);
        await user.paste(yaml);
      }
      const changes = () => screen.getByRole("heading", { name: "Changes" }).closest(".card") as HTMLElement;

      it("is locked until it is unlocked, and then says nothing has changed yet", async () => {
        const api = pausedApi();
        const user = renderConfigTab();
        expect(await screen.findByText(/Unlock it to change a setting; the page then says what each change does/)).toBeInTheDocument();
        expect(screen.queryByRole("heading", { name: "Changes" })).not.toBeInTheDocument();
        await user.click(screen.getByRole("tab", { name: "YAML" }));
        expect(screen.getByRole("textbox")).toHaveAttribute("readonly");

        await user.click(screen.getByRole("button", { name: "Unlock to edit" }));
        expect(screen.getByRole("heading", { name: "Configuration of this job, unlocked" })).toBeInTheDocument();
        expect(screen.getByText("You are editing this job's configuration. Nothing changes until you save.")).toBeInTheDocument();
        expect(screen.getByRole("textbox")).not.toHaveAttribute("readonly");
        expect(within(changes()).getByText("No changes yet.")).toBeInTheDocument();
        expect(within(changes()).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
        await user.click(within(changes()).getByRole("button", { name: "Lock again" }));
        expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
        expect(api.posted("/api/jobs/demo/config")).toEqual([]);
      });

      it("says what a change does to the pipeline, and saves it with a rerun of the stage it first affects", async () => {
        const api = pausedApi();
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        expect(await within(card).findByText("Output format")).toBeInTheDocument();
        expect(within(card).getByText("output.format")).toBeInTheDocument();
        expect(within(card).getByText("source")).toBeInTheDocument();
        expect(within(card).getByText("docx")).toBeInTheDocument();
        expect(within(card).getByText("First read by Build the book, which has finished. Only the output is rebuilt: seconds, no model call.")).toBeInTheDocument();
        expect(within(card).getByText(/the job is rerun from Build the book\./)).toBeInTheDocument();
        expect(api.posted("/api/jobs/demo/config/preview").at(-1)).toEqual({ text: NEXT });
        expect(within(card).queryByRole("button", { name: "Save" })).not.toBeInTheDocument();

        await user.click(within(card).getByRole("button", { name: "Save and rerun from Build the book" }));
        // The rerun dialog the Progress tab shows: what is redone, and what is lost with it.
        const dialog = await screen.findByRole("alertdialog");
        expect(within(dialog).getByText("Check the book")).toBeInTheDocument();
        expect(within(dialog).getByText("The compiled output is replaced by the new one.")).toBeInTheDocument();
        expect(api.posted("/api/jobs/demo/config")).toEqual([]); // nothing saved before the rerun is confirmed
        await user.click(within(dialog).getByRole("button", { name: /Rerun/ }));

        // One request saves and reruns: the server does both, by the effect it works out itself.
        await waitFor(() => expect(api.posted("/api/jobs/demo/config")).toEqual([{ text: NEXT, rerun: true, rerun_stages: ["compile"] }]));
        expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
        expect(await screen.findByText("Configuration saved. Rerunning from Build the book.")).toBeInTheDocument();
        await waitFor(() => expect(window.location.pathname).toBe("/jobs/demo/progress"));
      });

      it("saves nothing when the rerun is declined", async () => {
        const api = pausedApi();
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        await user.click(await within(changes()).findByRole("button", { name: "Save and rerun from Build the book" }));
        await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));
        expect(api.posted("/api/jobs/demo/config")).toEqual([]);
        expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
        expect(screen.getByRole("textbox")).toHaveValue(NEXT); // the edit is still there
      });

      it("saves a change that no finished stage read without a rerun", async () => {
        const api = pausedApi({
          "POST /api/jobs/demo/config/preview": {
            errors: [], locked: [], rerun_stages: [],
            changes: [change({ finished: false }), change({ path: "ollama.timeout_seconds", before: 1800, after: 900, stage: "", finished: false })],
          },
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        expect(await within(card).findByText("First read by Build the book, which has not finished: the change applies when it runs.")).toBeInTheDocument();
        expect(within(card).getByText("Applies to whatever runs next; nothing already done depends on it.")).toBeInTheDocument();
        expect(within(card).getByText("No finished stage read these settings, so nothing has to be rerun.")).toBeInTheDocument();
        expect(within(card).queryByRole("button", { name: /Save and rerun/ })).not.toBeInTheDocument();
        await user.click(within(card).getByRole("button", { name: "Save" }));
        await waitFor(() => expect(api.posted("/api/jobs/demo/config")).toEqual([{ text: NEXT, rerun: false, rerun_stages: [] }]));
        expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
        expect(await screen.findByText("Configuration saved. It applies to what runs next.")).toBeInTheDocument();
        // Saved: the page is the job's config again, locked.
        expect(await screen.findByRole("button", { name: "Unlock to edit" })).toBeInTheDocument();
        expect(window.location.pathname).toBe("/jobs/demo/config");
      });

      it("lists a config's problems and offers no save until they are fixed", async () => {
        pausedApi({
          "POST /api/jobs/demo/config/preview": {
            errors: [{ path: "ollama.temperature", message: "Input should be a valid number" }], changes: [], locked: [], rerun_stages: [],
          },
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        expect(await within(card).findByText("Fix these before saving:")).toBeInTheDocument();
        expect(within(card).getByText("Input should be a valid number")).toBeInTheDocument();
        expect(within(card).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
        await user.click(within(card).getByRole("button", { name: "Discard changes" }));
        expect(screen.getByRole("textbox")).toHaveValue(YAML);
        expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
      });

      it("will not save a locked setting that was changed in the YAML, and says why it is locked", async () => {
        pausedApi({
          "POST /api/jobs/demo/config/preview": {
            errors: [], locked: ["translation.direction"], rerun_stages: [],
            changes: [change({ path: "translation.direction", before: "en-zh", after: "en>de", stage: "translate", locked: "direction" })],
          },
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        expect(await within(card).findByText("Locked once a job has started: another pair of languages is another job.")).toBeInTheDocument();
        expect(within(card).getByText("A locked setting was changed. Put it back to save the others.")).toBeInTheDocument();
        expect(within(card).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
      });

      it("keeps the locked settings locked in the form, each with its reason", async () => {
        pausedApi();
        const user = renderConfigTab();
        await user.click(await screen.findByRole("button", { name: "Unlock to edit" }));
        // The languages cannot be changed; another setting can.
        expect(await screen.findByRole("combobox", { name: "Into language" })).toBeDisabled();
        expect(screen.getAllByText("Locked once a job has started: another pair of languages is another job.").length).toBeGreaterThan(0);
        const style = screen.getByText("translation.style", { selector: ".path" }).closest(".opt") as HTMLElement;
        // Once the file's values have reached the form.
        await waitFor(() => expect(within(style).getByRole("textbox")).toBeEnabled());
      });

      it("says the save failed and keeps the edit when the server refuses it", async () => {
        const api = pausedApi({
          "POST /api/jobs/demo/config/preview": { errors: [], changes: [change({ finished: false })], locked: [], rerun_stages: [] },
          "POST /api/jobs/demo/config": apiError("the job is running; pause or stop it before changing its configuration"),
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        await user.click(await within(changes()).findByRole("button", { name: "Save" }));
        expect(await screen.findByText("the job is running; pause or stop it before changing its configuration")).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue(NEXT);
        expect(api.posted("/api/jobs/demo/rerun")).toEqual([]);
      });

      it("offers no save by a preview of an earlier text: an edit made since has to be previewed first", async () => {
        // Reviewed on PR #11: the Save of the first preview was still there, and saved the later text with the first rerun.
        const LATER = NEXT + "translation:\n  style: concise\n";
        const second = deferred<unknown>();
        const api = pausedApi({
          "POST /api/jobs/demo/config/preview": (body: { text: string }) =>
            body.text === NEXT
              ? { errors: [], changes: [change()], locked: [], rerun_stages: ["compile"] }
              : second.promise,
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        expect(await within(card).findByRole("button", { name: "Save and rerun from Build the book" })).toBeInTheDocument();

        const editor = screen.getByRole("textbox");
        await user.clear(editor);
        await user.click(editor);
        await user.paste(LATER);
        // At once, before the new preview is even asked for: nothing to save by.
        expect(within(card).queryByRole("button", { name: /Save/ })).not.toBeInTheDocument();
        expect(within(card).getByText("Checking what this changes…")).toBeInTheDocument();
        expect(within(card).queryByText("Output format")).not.toBeInTheDocument();

        second.resolve({
          errors: [], locked: [], rerun_stages: ["translate"],
          changes: [change(), change({ path: "translation.style", before: "literary", after: "concise", stage: "translate" })],
        });
        const save = await within(card).findByRole("button", { name: "Save and rerun from Translate" });
        expect(within(card).getByText("First read by Translate, which has finished. The whole text is translated again.")).toBeInTheDocument();
        expect(api.posted("/api/jobs/demo/config")).toEqual([]);
        expect(save).toBeEnabled();
      });

      it("reruns from both branches of the pipeline when the changes reach both, and says so", async () => {
        const api = pausedApi({
          "POST /api/jobs/demo/config/preview": {
            errors: [], locked: [], rerun_stages: ["extract_glossary", "build_story_context"],
            changes: [
              change({ path: "glossary.extraction_max_entries", before: 100, after: 77, stage: "extract_glossary", stages: ["extract_glossary"] }),
              change({ path: "ollama.model", before: "qwen3:14b", after: "other:1b", stage: "resolve_glossary", stages: ["resolve_glossary", "build_story_context"] }),
            ],
          },
        });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        const card = changes();
        // A setting two branches read names both, with what each costs.
        expect(await within(card).findByText(/^First read by Resolve glossary and Build story context, which has finished\. The glossary is resolved again.* The story summaries are written again/)).toBeInTheDocument();
        await user.click(within(card).getByRole("button", { name: "Save and rerun from Extract glossary and Build story context" }));
        const dialog = await screen.findByRole("alertdialog");
        expect(within(dialog).getByRole("heading", { name: "Rerun from Extract glossary and Build story context?" })).toBeInTheDocument();
        expect(api.requested("/api/jobs/demo/rerun?stage=extract_glossary&also=build_story_context")).toBe(true);
        await user.click(within(dialog).getByRole("button", { name: /Rerun/ }));
        await waitFor(() => expect(api.posted("/api/jobs/demo/config")).toEqual([
          { text: NEXT, rerun: true, rerun_stages: ["extract_glossary", "build_story_context"] },
        ]));
      });

      it("says so and keeps the edit when the server finds another effect than the one shown", async () => {
        pausedApi({ "POST /api/jobs/demo/config": apiError("these changes do not have the effect that was shown; look at the list of changes again") });
        const user = renderConfigTab();
        await unlockAndWrite(user, NEXT);
        await user.click(await within(changes()).findByRole("button", { name: "Save and rerun from Build the book" }));
        await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: /Rerun/ }));
        expect(await screen.findByText(/do not have the effect that was shown/)).toBeInTheDocument();
        expect(screen.getByRole("textbox")).toHaveValue(NEXT);
        expect(window.location.pathname).toBe("/jobs/demo/config");
      });

      it("lists the changes saved to this job's config before", async () => {
        pausedApi({
          "GET /api/jobs/demo/config": {
            editable: false, name: "demo.yaml", text: YAML, validated: true, unlockable: true, locked: {},
            changes: [
              { at: "2026-10-01T10:00:00+00:00", changes: [{ path: "output.format", before: "source", after: "docx", stage: "compile" }] },
              { at: "2026-10-02T10:00:00+00:00", changes: [{ path: "ollama.timeout_seconds", before: 1800, after: 900, stage: "" }] },
            ],
          },
        });
        renderConfigTab();
        const history = await screen.findByText("2 earlier changes to this configuration");
        const card = history.closest(".card") as HTMLElement;
        expect(within(card).getByText("output.format")).toBeInTheDocument();
        expect(within(card).getByText("ollama.timeout_seconds")).toBeInTheDocument();
      });
    });

    it("can be unlocked once the job it was opened on has stopped running", async () => {
      // Reviewed on PR #11: the page kept what it was told when it opened, and the button stayed disabled.
      let running = true;
      configApi({
        "GET /api/jobs/demo/info": () => jobInfo({ overall: running ? "running" : "paused", running, can_stop: running, source_path: SOURCE }),
        "GET /api/jobs/demo/config": { editable: false, name: "demo.yaml", text: YAML, validated: true, unlockable: false, locked: {}, changes: [] },
        "POST /api/jobs/demo/pause": () => { running = false; return { requested: true }; },
      });
      const user = renderConfigTab();
      const unlock = await screen.findByRole("button", { name: "Unlock to edit" });
      expect(unlock).toBeDisabled();
      expect(screen.getByText("The job is running. Pause or stop it to change its configuration.")).toBeInTheDocument();
      // Pausing from the header reloads the job; the page goes by the job as it now stands.
      await user.click(screen.getByRole("button", { name: "❚❚ Pause" }));
      await waitFor(() => expect(screen.getByRole("button", { name: "Unlock to edit" })).toBeEnabled());
      await user.click(screen.getByRole("button", { name: "Unlock to edit" }));
      expect(screen.getByRole("heading", { name: "Configuration of this job, unlocked" })).toBeInTheDocument();
    });

    it("does not show a failed-start banner for a job whose later command failed", async () => {
      configApi({
        "GET /api/jobs/demo/info": jobInfo({
          overall: "failed",
          process: { label: "resume", running: false, exit_code: 1, outcome: "failed", started: "10:00", output_tail: "boom" },
        }),
        "GET /api/jobs/demo/config": { editable: false, name: "demo.yaml", text: YAML, validated: true },
      });
      renderConfigTab();
      await screen.findByRole("heading", { name: "Configuration used by this run" });
      expect(screen.queryByText(/The last start failed/)).not.toBeInTheDocument();
    });
  });

  it("reports a config that cannot be loaded", async () => {
    configApi({ "GET /api/jobs/demo/config": apiError("demo.yaml is missing", 404) });
    renderConfigTab();
    expect(await screen.findByText("demo.yaml is missing")).toBeInTheDocument();
    expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Configuration/ })).not.toBeInTheDocument();
  });
});

// -- journeys -------------------------------------------------------------------
// README: "pick the two languages under Languages on a job's Config tab"; "Validate
// saves the file, checks it, dry-runs the job"; "Start translation unlocks only
// while the config is unchanged since it passed".

describe("Config tab: setting a book up", () => {
  const LANGUAGES = [
    { code: "en", name: "English", tier: "tuned" },
    { code: "zh", name: "Simplified Chinese", tier: "tuned" },
    { code: "fr", name: "French", tier: "profiled" },
    { code: "ja", name: "Japanese", tier: "profiled" },
    { code: "de", name: "German", tier: "profiled" },
  ];
  const named = (code: string) => LANGUAGES.find((l) => l.code === code) ?? { code, name: code, tier: "generic" };
  const supportFor = (pair: string) => {
    const [source, target] = pair.includes(">") ? pair.split(">") : pair.split("-");
    const tuned = pair === "en-zh" || pair === "zh-en";
    return {
      pair, source: named(source), target: named(target),
      skipped: tuned ? [] : [{ check: "prose rewrite", reason: "its prompt and rules are written for Chinese" }],
      notice: tuned ? "" : "Translation quality for this pair depends on the model.",
    };
  };

  /** A draft whose YAML and validation state live on a pretend server. */
  function draftBook(overrides: Record<string, unknown> = {}) {
    const server = { validated: false, started: false };
    const api = configApi({
      "GET /api/jobs/demo/info": () =>
        server.started
          ? jobInfo({ overall: "running", running: true, can_stop: true, source_path: SOURCE })
          : draft({ validated: server.validated }),
      "GET /api/languages": (_: unknown, url: URL) => ({ languages: LANGUAGES, support: supportFor(url.searchParams.get("pair") || "en-zh"), error: "" }),
      "POST /api/config/dump": (body: { values: { translation?: { direction?: string } } }) => ({
        text: `${YAML}translation:\n  direction: ${body.values.translation?.direction}\n`,
      }),
      "POST /api/jobs/demo/validate": (body: { text: string }) => {
        server.validated = true;
        const direction = /direction: (\S+)/.exec(body.text)?.[1] ?? "en-zh";
        return check({ summary: { ...check().summary, direction, languages: supportFor(direction) } });
      },
      "POST /api/jobs/demo/start": () => { server.started = true; return { started: true }; },
      ...overrides,
    });
    return { api, server };
  }

  it("a translator sets the book to French into Japanese, validates it, and starts the translation", async () => {
    const { api } = draftBook();
    const user = renderConfigTab();
    await screen.findByRole("heading", { name: "Configuration" });
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();

    // Pick the two languages, once the page has read the book's config and lets them be changed.
    const from = await screen.findByRole("combobox", { name: "From language" });
    await waitFor(() => expect(from).toBeEnabled());
    await user.clear(from);
    await user.type(from, "fr");
    await user.tab();
    const into = screen.getByRole("combobox", { name: "Into language" });
    await user.clear(into);
    await user.type(into, "ja");
    await user.tab();
    expect(await screen.findByText("French")).toBeInTheDocument();
    expect(screen.getByText("Japanese")).toBeInTheDocument();

    // The page warns what this pair cannot do, and that the change is not saved yet.
    expect(await screen.findByText("Translation quality for this pair depends on the model.")).toBeInTheDocument();
    expect(await within(validateCard()).findByText(/Unsaved changes\./)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Save & validate" }));
    const card = within(validateCard());
    expect(await card.findByText(/at the top is ready\./)).toBeInTheDocument();
    expect(card.getByText("FR → JA")).toBeInTheDocument();
    expect(card.getByText("1 check skipped for this pair")).toBeInTheDocument();
    expect(api.posted("/api/jobs/demo/validate")).toEqual([{ text: `${YAML}translation:\n  direction: fr>ja\n` }]);

    // Start, and follow the run on the Progress tab.
    await user.click(screen.getByRole("button", { name: "Start translation" }));
    await waitFor(() => expect(window.location.pathname).toBe("/jobs/demo/progress"));
    expect(screen.getByRole("status")).toHaveTextContent("Translation started.");
    expect(await screen.findByRole("button", { name: "❚❚ Pause" })).toBeInTheDocument();
  });

  it("a translator is told a model is missing, corrects its name in the YAML, and validates again", async () => {
    const missing = check({
      ok: false, validated: false,
      problems: ["model gemma3:27b is not installed in Ollama"],
      models: [{ role: "primary", model: "qwen3:14b", installed: true }, { role: "audit", model: "gemma3:27b", installed: false }],
    });
    const { api } = draftBook({
      "GET /api/jobs/demo/config": { editable: true, name: "demo.yaml", text: `${YAML}audit:\n  model: gemma3:27b\n`, validated: false },
      "POST /api/jobs/demo/validate": (body: { text: string }) => (body.text.includes("gemma3:27b") ? missing : check()),
      "GET /api/jobs/demo/info": () => draft({ validated: api.posted("/api/jobs/demo/validate").length > 1 }),
    });
    const user = renderConfigTab();
    await user.click(await screen.findByRole("button", { name: "Validate" }));

    const card = within(validateCard());
    expect(await card.findByText(/Not ready yet:/)).toHaveTextContent("model gemma3:27b is not installed in Ollama");
    expect(within(card.getByRole("cell", { name: "gemma3:27b" }).closest("tr") as HTMLElement).getByText("✗ missing")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Start translation" })).toBeDisabled();

    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const yaml = screen.getByRole("textbox");
    await user.clear(yaml);
    await user.type(yaml, `${YAML}audit:{Enter}  model: gemma3:12b{Enter}`);
    await user.click(screen.getByRole("button", { name: "Save & validate" }));

    expect(await card.findByText(/at the top to run/)).toBeInTheDocument();
    expect(card.queryByText(/Not ready yet:/)).not.toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("button", { name: "Start translation" })).toBeEnabled());
  });

  it("a translator tweaks the config after validating and has to validate again before starting", async () => {
    draftBook();
    const user = renderConfigTab();
    await user.click(await screen.findByRole("button", { name: "Validate" }));
    await within(validateCard()).findByText(/at the top is ready\./);
    expect(screen.getByRole("button", { name: "Start translation" })).toBeEnabled();

    await editYaml(user, "# use the literary style\n");
    expect(within(validateCard()).getByText(/Unsaved changes\./)).toHaveTextContent("until then the job would start with the last validated file");
    await user.click(screen.getByRole("button", { name: "Save & validate" }));
    expect(await within(validateCard()).findByText(/at the top is ready\./)).toBeInTheDocument();
  });

  it("a colleague opens the config of a book that is already translating: everything can be read, nothing changed", async () => {
    draftBook({
      "GET /api/jobs/demo/info": jobInfo({ overall: "running", running: true, can_stop: true, source_path: SOURCE }),
      "GET /api/jobs/demo/config": { editable: false, name: "demo.yaml", text: YAML, validated: true },
    });
    const user = renderConfigTab();
    expect(await screen.findByRole("heading", { name: "Configuration used by this run" })).toBeInTheDocument();
    expect(screen.getByText(BOOK.title)).toBeInTheDocument();
    expect(await screen.findByRole("combobox", { name: "Into language" })).toBeDisabled();

    // Folding a group and reading the YAML still work.
    const models = screen.getByRole("button", { name: /▾\s*Models/ });
    await user.click(models);
    expect(models).toHaveAttribute("aria-expanded", "false");
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByRole("textbox")).toHaveValue(YAML);
    expect(screen.queryByRole("button", { name: /Validate/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "❚❚ Pause" })).toBeEnabled();
  });
});
