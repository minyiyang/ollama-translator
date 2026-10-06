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
      expect(screen.getByText("The run captured this configuration when it started; it is read-only. To change it, create a new job.")).toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Validate" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /Validate/ })).not.toBeInTheDocument();

      await user.click(screen.getByRole("tab", { name: "YAML" }));
      const yaml = screen.getByRole("textbox");
      expect(yaml).toHaveValue(YAML);
      expect(yaml).toHaveAttribute("readonly");
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
