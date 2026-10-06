import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { apiError, deferred, jsonResponse, mockApi } from "../test/mockApi";
import { ConfigEditor } from "./ConfigEditor";

// The Config tab for any language pair (docs/GENERIC_LANGUAGES.md, phase 4).

const field = (path: string, type: string, value: unknown, extra: Record<string, unknown> = {}) => ({
  path, key: path.split(".").at(-1), type, default: value, nullable: false, ...extra,
});

// The schema is fetched once per page load, so every test in this file sees this one.
const SCHEMA = {
  sections: [
    {
      key: "translation", title: "Translation", fields: [
        field("translation.direction", "string", "en-zh"),
        field("translation.style", "string", "literary"),
        field("translation.thinking", "boolean", false),
        field("translation.de_ai_strength", "enum", "conservative", { enum: ["conservative", "moderate"] }),
      ],
    },
    { key: "reprose", title: "Prose rewrite", fields: [field("reprose.enabled", "boolean", false)] },
    { key: "consistency", title: "Book consistency", fields: [field("consistency.conventions", "boolean", true)] },
    {
      key: "ollama", title: "Ollama", fields: [
        field("ollama.model", "string", "qwen3:14b"),
        field("ollama.host", "string", "http://localhost:11434"),
        field("ollama.num_ctx", "integer", 8192, { minimum: 512 }),
      ],
    },
    {
      key: "workflow", title: "Workflow", fields: [
        field("workflow.require_glossary_review", "boolean", true),
        field("workflow.llm_glossary_review", "boolean", false),
      ],
    },
    { key: "epub", title: "EPUB", fields: [field("epub.chapter_title_template", "string", "{n}")] },
  ],
};

const HOST = "http://ollama.test:11434";

function editorApi(direction: string, overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/config/schema": SCHEMA,
    "POST /api/config/parse": { values: { translation: { direction } }, syntax_error: null },
    "POST /api/config/check": { errors: [] },
    "GET /api/models": { installed: [] },
    "GET /api/languages": (_: unknown, url: URL) => {
      const pair = url.searchParams.get("pair") ?? "";
      const generic = pair !== "en-zh";
      return {
        languages: [
          { code: "en", name: "English", tier: "tuned" },
          { code: "zh", name: "Simplified Chinese", tier: "tuned" },
          { code: "ja", name: "Japanese", tier: "generic" },
        ],
        support: {
          pair,
          source: { code: "en", name: "English", tier: "tuned" },
          target: generic ? { code: "ja", name: "Japanese", tier: "generic" } : { code: "zh", name: "Simplified Chinese", tier: "tuned" },
          skipped: generic
            ? [
                { check: "prose rewrite", reason: "its prompt and rules are written for Chinese; reprose stays off" },
                { check: "punctuation conventions", reason: "no house conventions for Korean" },
              ]
            : [],
          notice: generic ? "Quality depends on the local model." : "",
        },
        error: "",
      };
    },
    ...overrides,
  });
}

describe("Config editor: options that depend on the languages", () => {
  it("hides the options a generic pair cannot use and says why", async () => {
    editorApi("en>ja");
    render(<ConfigEditor text={"translation:\n  direction: en>ja\n"} onTextChange={() => {}} hasComments={false} />);
    // Prose rewrite is both a model and a quality check: each group says why it is gone.
    expect(await screen.findAllByText(/Not used for en>ja: Prose rewrite \(its prompt/)).toHaveLength(2);
    expect(screen.getByText(/Not used for en>ja: Punctuation conventions/)).toBeTruthy();
    expect(screen.queryByText("reprose.enabled")).toBeNull();
    expect(screen.queryByText("consistency.conventions")).toBeNull();
    expect(screen.getByText("Quality depends on the local model.")).toBeTruthy();
  });

  it("shows every option for a tuned pair", async () => {
    editorApi("en-zh");
    render(<ConfigEditor text={"translation:\n  direction: en-zh\n"} onTextChange={() => {}} hasComments={false} />);
    expect(await screen.findByText("reprose.enabled")).toBeTruthy();
    expect(screen.getByText("consistency.conventions")).toBeTruthy();
    expect(screen.queryByText(/Not used for/)).toBeNull();
  });
});

// -- the form -----------------------------------------------------------------

const YAML = "ollama:\n  host: http://ollama.test:11434\n";
type Values = Record<string, Record<string, unknown>>;

/** The form parses to these values; the dump echoes values back as JSON so a test can read what was written. */
function formApi(values: Values = {}, overrides: Record<string, unknown> = {}) {
  return editorApi("en-zh", {
    "POST /api/config/parse": { values: { ollama: { host: HOST }, ...values }, syntax_error: null },
    "POST /api/config/dump": (body: { values: Values }) => ({ text: JSON.stringify(body.values) }),
    ...overrides,
  });
}

/** Owns the YAML text the way the Config page does. */
function Harness({ onText, ...props }: { onText: (text: string) => void; hasComments?: boolean; readOnly?: boolean }) {
  const [text, setText] = useState(YAML);
  return <ConfigEditor hasComments={false} {...props} text={text} onTextChange={(next) => { setText(next); onText(next); }} />;
}

/** Render the editor and wait until the parsed values have reached the form. */
async function renderEditor(props: { hasComments?: boolean; readOnly?: boolean } = {}) {
  const onText = vi.fn();
  const user = userEvent.setup();
  render(<Harness onText={onText} {...props} />);
  await screen.findByDisplayValue(HOST);
  return { user, onText };
}

const option = (path: string) => screen.getByText(path, { selector: ".path" }).closest(".opt") as HTMLElement;
const group = (title: string) => screen.getByRole("button", { name: new RegExp(`▾\\s*${title}`) });
const written = (api: ReturnType<typeof formApi>) => (api.posted("/api/config/dump") as { values: Values }[]).map((body) => body.values);
const lastWritten = (api: ReturnType<typeof formApi>) => written(api).at(-1);

beforeEach(() => {
  Element.prototype.scrollIntoView = vi.fn(); // not implemented by jsdom; the section links use it
});

describe("Config editor: the Options tab", () => {
  it("opens on the Options tab with labelled, explained settings in groups", async () => {
    formApi();
    await renderEditor();
    expect(screen.getByRole("tab", { name: "Options" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("tab", { name: "All settings" })).toHaveAttribute("aria-selected", "false");

    const model = within(option("ollama.model"));
    expect(model.getByText("Primary model")).toBeInTheDocument();
    expect(model.getByText("Translates, resolves and approves the glossary, and repairs unless overridden.")).toBeInTheDocument();
    expect(model.getByRole("combobox")).toHaveValue("qwen3:14b"); // the schema default
    expect(group("Models")).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Each model must be installed in Ollama. ✓ / ✗ shows what the configured host reports.")).toBeInTheDocument();
    // Settings the schema does not have are simply not offered.
    expect(screen.queryByText("glossary.extraction_model")).not.toBeInTheDocument();
  });

  it("turning an option on rewrites the config file and marks its group as changed", async () => {
    const api = formApi();
    const { user, onText } = await renderEditor();
    expect(group("Translation")).not.toHaveTextContent("changed");

    await user.click(within(option("translation.thinking")).getByRole("checkbox", { name: "translation.thinking" }));
    await waitFor(() => expect(onText).toHaveBeenCalled());
    expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, translation: { thinking: true } });
    expect(onText).toHaveBeenLastCalledWith(JSON.stringify({ ollama: { host: HOST }, translation: { thinking: true } }));
    expect(group("Translation")).toHaveTextContent("1 changed");
    expect(within(option("translation.thinking")).getByRole("checkbox")).toBeChecked();
  });

  it("does not ask the server to read back a change the form just made", async () => {
    const api = formApi();
    const { user, onText } = await renderEditor();
    await user.click(within(option("translation.thinking")).getByRole("checkbox"));
    await waitFor(() => expect(onText).toHaveBeenCalled());
    await new Promise((done) => setTimeout(done, 400)); // past the parse debounce
    expect(api.posted("/api/config/parse")).toEqual([{ text: YAML }]);
  });

  it("Reset puts an option back to its default and takes it out of the config file", async () => {
    const api = formApi({ translation: { de_ai_strength: "moderate" } });
    const { user } = await renderEditor();
    const row = within(option("translation.de_ai_strength"));
    expect(row.getByRole("combobox")).toHaveValue("moderate");
    const reset = row.getByRole("button", { name: "Reset" });
    expect(reset).toHaveAttribute("title", 'Default: "conservative"');

    await user.click(reset);
    await waitFor(() => expect(lastWritten(api)).toEqual({ ollama: { host: HOST } })); // the emptied section goes too
    expect(row.getByRole("combobox")).toHaveValue("conservative");
    expect(row.queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
  });

  it("offers no reset for a value that equals the default", async () => {
    formApi({ translation: { de_ai_strength: "conservative" } });
    await renderEditor();
    expect(within(option("translation.de_ai_strength")).queryByRole("button", { name: "Reset" })).not.toBeInTheDocument();
    expect(group("Translation")).not.toHaveTextContent("changed");
  });

  it("writes a picked enum value", async () => {
    const api = formApi();
    const { user } = await renderEditor();
    await user.selectOptions(within(option("translation.de_ai_strength")).getByRole("combobox"), "moderate");
    await waitFor(() => expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, translation: { de_ai_strength: "moderate" } }));
  });

  describe("glossary approval", () => {
    const approval = () => within(screen.getByText("Glossary approval").closest(".opt") as HTMLElement);

    it("shows who approves the glossary, and what that means for the run", async () => {
      formApi();
      await renderEditor();
      expect(approval().getByRole("radio", { name: "I review" })).toBeChecked(); // the defaults: review required, not by the LLM
      expect(approval().getByText("The run pauses so you can review the glossary on the Glossary page.")).toBeInTheDocument();
    });

    it.each([
      ["LLM reviews", { require_glossary_review: true, llm_glossary_review: true }, "The LLM reviewer approves the glossary and the run continues."],
      ["No review", { require_glossary_review: false, llm_glossary_review: false }, "The draft glossary is used unreviewed. Only for already-reviewed glossaries."],
    ])("choosing “%s” is written to the config", async (label, flags, help) => {
      const api = formApi();
      const { user } = await renderEditor();
      await user.click(approval().getByRole("radio", { name: label }));
      await waitFor(() => expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, workflow: flags }));
      expect(approval().getByRole("radio", { name: label })).toBeChecked();
      expect(approval().getByText(help)).toBeInTheDocument();
    });

    it("shows “LLM reviews” for a config that says so", async () => {
      formApi({ workflow: { require_glossary_review: true, llm_glossary_review: true } });
      await renderEditor();
      expect(approval().getByRole("radio", { name: "LLM reviews" })).toBeChecked();
    });
  });

  describe("language pair", () => {
    it("shows the two languages of a config that names them one by one", async () => {
      formApi({ translation: { source_language: "zh", target_language: "en" } });
      await renderEditor();
      await waitFor(() => expect(screen.getByRole("combobox", { name: "From language" })).toHaveValue("zh"));
      expect(screen.getByRole("combobox", { name: "Into language" })).toHaveValue("en");
    });

    it("picking a new language rewrites the pair in one place, not two", async () => {
      const api = formApi({ translation: { source_language: "en", target_language: "zh", style: "plain" } });
      const { user } = await renderEditor();
      const into = screen.getByRole("combobox", { name: "Into language" });
      await user.clear(into);
      await user.type(into, "ja");
      await waitFor(() => expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, translation: { style: "plain", direction: "en>ja" } }));
    });
  });

  describe("groups", () => {
    it("folds and unfolds one group, keeping its counts in the header", async () => {
      formApi({ translation: { thinking: true } });
      const { user } = await renderEditor();
      const translation = group("Translation");
      expect(translation).toHaveTextContent("1 changed");

      await user.click(translation);
      expect(translation).toHaveAttribute("aria-expanded", "false");
      expect(screen.queryByText("translation.thinking")).not.toBeInTheDocument();
      expect(translation).toHaveTextContent("1 changed");
      expect(screen.getByText("ollama.model")).toBeInTheDocument(); // other groups stay open

      await user.click(translation);
      expect(translation).toHaveAttribute("aria-expanded", "true");
      expect(screen.getByText("translation.thinking")).toBeInTheDocument();
    });

    it("folds and unfolds every group at once", async () => {
      formApi();
      const { user } = await renderEditor();
      await user.click(screen.getByRole("button", { name: "Collapse all" }));
      expect(screen.queryByText("ollama.model")).not.toBeInTheDocument();
      expect(screen.queryByText("translation.thinking")).not.toBeInTheDocument();
      for (const title of ["Translation", "Models", "Glossary"]) expect(group(title)).toHaveAttribute("aria-expanded", "false");

      await user.click(screen.getByRole("button", { name: "Expand all" }));
      expect(screen.getByText("ollama.model")).toBeInTheDocument();
      expect(group("Translation")).toHaveAttribute("aria-expanded", "true");
    });
  });
});

describe("Config editor: problems with the config", () => {
  it("shows a setting's error on its row and counts it on its group", async () => {
    formApi({}, { "POST /api/config/check": { errors: [{ path: "ollama.num_ctx", message: "Input should be greater than or equal to 512" }] } });
    await renderEditor();
    expect(await within(option("ollama.num_ctx")).findByText("Input should be greater than or equal to 512")).toBeInTheDocument();
    expect(group("Ollama and context")).toHaveTextContent("1 invalid");
    expect(group("Models")).not.toHaveTextContent("invalid");
  });

  it("lists errors that belong to no setting above the form", async () => {
    formApi({}, {
      "POST /api/config/check": { errors: [{ path: "", message: "source file is missing" }, { path: "ollama.modle", message: "Extra inputs are not permitted" }] },
    });
    await renderEditor();
    const banner = (await screen.findByText("source file is missing")).parentElement as HTMLElement;
    expect([...banner.children].map((line) => line.textContent)).toEqual(["source file is missing", "ollama.modle: Extra inputs are not permitted"]);
  });

  it("shows the language pair's error under the pair", async () => {
    formApi({}, { "POST /api/config/check": { errors: [{ path: "translation.direction", message: "unknown language code 'xx'" }] } });
    await renderEditor();
    const pair = screen.getByText("Languages", { selector: ".name" }).closest(".opt") as HTMLElement;
    expect(await within(pair).findByText("unknown language code 'xx'")).toBeInTheDocument();
    expect(group("Translation")).toHaveTextContent("1 invalid");
  });

  it("checks the text with the server after each change", async () => {
    const api = formApi();
    const { user } = await renderEditor();
    await waitFor(() => expect(api.posted("/api/config/check")).toEqual([{ text: YAML }]));

    await user.click(within(option("translation.thinking")).getByRole("checkbox"));
    await waitFor(() => expect(api.posted("/api/config/check")).toHaveLength(2));
    expect(api.posted("/api/config/check")[1]).toEqual({ text: JSON.stringify({ ollama: { host: HOST }, translation: { thinking: true } }) });
  });

  it("says the form shows its last valid state when the YAML has a syntax error, and leads to the YAML tab", async () => {
    const error = { message: "mapping values are not allowed here", line: 3, column: 7, context_line: null };
    mockApi({
      "GET /api/config/schema": SCHEMA,
      "POST /api/config/parse": { values: null, syntax_error: error },
      "POST /api/config/check": { errors: [{ path: "", message: "cannot validate invalid YAML" }] },
      "GET /api/models": { installed: [] },
      "GET /api/languages": { languages: [], support: null, error: "" },
    });
    const user = userEvent.setup();
    render(<Harness onText={() => {}} />);
    const banner = await screen.findByText(/The YAML has a syntax error on line 3, so the form shows its last valid state\./);
    expect(screen.queryByText("cannot validate invalid YAML")).not.toBeInTheDocument(); // one problem at a time
    expect(screen.getByText("ollama.model")).toBeInTheDocument(); // the form is still there

    await user.click(within(banner).getByRole("button", { name: "Open the YAML tab" }));
    expect(screen.getByRole("tab", { name: "YAML" })).toHaveAttribute("aria-selected", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Syntax error on line 3, column 7: mapping values are not allowed here");
    expect(screen.queryByText(/so the form shows its last valid state/)).not.toBeInTheDocument();
  });

  it("says the server could not be reached, rather than blaming the config", async () => {
    mockApi({
      "GET /api/config/schema": SCHEMA,
      "POST /api/config/parse": apiError("server restarting", 503),
      "POST /api/config/check": apiError("server restarting", 503),
      "GET /api/models": apiError("server restarting", 503),
      "GET /api/languages": { languages: [], support: null, error: "" },
    });
    render(<Harness onText={() => {}} />);
    expect(await screen.findByText("Could not check the configuration with the server: server restarting")).toBeInTheDocument();
    expect(screen.queryByText(/syntax error/)).not.toBeInTheDocument();
  });
});

describe("Config editor: which models are installed", () => {
  it("asks the configured Ollama host which models are installed and marks each model setting", async () => {
    const api = formApi({ ollama: { host: HOST, model: "phi4" } }, { "GET /api/models": { installed: ["qwen3:14b", "gemma3:latest"] } });
    await renderEditor();
    expect(await within(option("ollama.model")).findByText("✗ not installed", {}, { timeout: 3000 })).toBeInTheDocument();
    expect(api.requested(`/api/models?host=${encodeURIComponent(HOST)}`)).toBe(true);
    expect([...document.querySelectorAll("#installed-models option")].map((o) => o.getAttribute("value"))).toEqual(["qwen3:14b", "gemma3:latest"]);
    expect(within(option("ollama.host")).queryByText(/✓|✗/)).not.toBeInTheDocument(); // not a model setting
  });

  it("marks an installed model", async () => {
    formApi({ ollama: { host: HOST, model: "qwen3:14b" } }, { "GET /api/models": { installed: ["qwen3:14b"] } });
    await renderEditor();
    expect(await within(option("ollama.model")).findByTitle("Installed", {}, { timeout: 3000 })).toBeInTheDocument();
  });

  it("marks nothing when Ollama cannot be asked", async () => {
    const api = formApi({ ollama: { host: HOST, model: "qwen3:14b" } }, { "GET /api/models": apiError("connection refused", 502) });
    await renderEditor();
    await waitFor(() => expect(api.requested(`/api/models?host=${encodeURIComponent(HOST)}`)).toBe(true), { timeout: 3000 });
    expect(within(option("ollama.model")).queryByText(/✓|✗/)).not.toBeInTheDocument();
  });
});

describe("Config editor: the All settings tab", () => {
  const openAll = async (props: Parameters<typeof renderEditor>[0] = {}, values: Values = {}) => {
    const api = formApi(values);
    const view = await renderEditor(props);
    await view.user.click(screen.getByRole("tab", { name: "All settings" }));
    return { ...view, api };
  };
  const shownPaths = () => [...document.querySelectorAll(".opt .path")].map((node) => node.textContent);

  it("lists every setting there is, section by section, each with a readable name", async () => {
    await openAll();
    expect(shownPaths()).toEqual(SCHEMA.sections.flatMap((section) => section.fields.map((f) => f.path)));
    expect(group("EPUB")).toHaveTextContent("EPUB epub"); // the section's title and its YAML key
    expect(within(option("epub.chapter_title_template")).getByText("Chapter title template")).toBeInTheDocument();
    expect(within(option("ollama.num_ctx")).getByText("Context ceiling")).toBeInTheDocument();
    expect(within(option("ollama.num_ctx")).getByText("≥ 512")).toBeInTheDocument();
  });

  it("finds a setting by its config key or by its name, hiding the sections without a match", async () => {
    const { user } = await openAll();
    const filter = screen.getByPlaceholderText("Filter settings, e.g. num_ctx");
    await user.type(filter, "NUM_ctx");
    expect(shownPaths()).toEqual(["ollama.num_ctx"]);
    expect(screen.queryByRole("button", { name: /▾\s*Translation/ })).not.toBeInTheDocument();

    await user.clear(filter);
    await user.type(filter, "primary"); // matches the label "Primary model", not the path
    expect(shownPaths()).toEqual(["ollama.model"]);

    await user.clear(filter);
    await user.type(filter, "no such setting");
    expect(shownPaths()).toEqual([]);
  });

  it("can show just the settings this book changed", async () => {
    const { user } = await openAll({}, { translation: { thinking: true, style: "literary" } });
    await user.click(screen.getByRole("checkbox", { name: "Only changed from default" }));
    expect(shownPaths()).toEqual(["translation.thinking", "ollama.host"]); // style is set, but to its default

    await user.click(screen.getByRole("checkbox", { name: "Only changed from default" }));
    expect(shownPaths()).toHaveLength(12);
  });

  it("edits a setting that has no place on the Options tab", async () => {
    const { user, api } = await openAll();
    await user.type(within(option("epub.chapter_title_template")).getByRole("textbox"), "!");
    await waitFor(() => expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, epub: { chapter_title_template: "{n}!" } }));
    expect(group("EPUB")).toHaveTextContent("1 changed");
  });

  it("folds every section, and a section link unfolds its own and scrolls to it", async () => {
    const { user } = await openAll();
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(shownPaths()).toEqual([]);

    await user.click(screen.getByRole("button", { name: "Workflow" }));
    expect(shownPaths()).toEqual(["workflow.require_glossary_review", "workflow.llm_glossary_review"]);
    await waitFor(() => expect(Element.prototype.scrollIntoView).toHaveBeenCalledWith({ behavior: "smooth" }));
    expect(vi.mocked(Element.prototype.scrollIntoView).mock.contexts[0]).toBe(document.getElementById("sec-workflow"));

    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(shownPaths()).toHaveLength(12);
  });
});

describe("Config editor: the YAML tab", () => {
  it("edits the text directly and has the server parse it again", async () => {
    const api = formApi();
    const { user, onText } = await renderEditor();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    const yaml = screen.getByRole("textbox");
    expect(yaml).toHaveValue(YAML);
    expect(screen.queryByText("ollama.model")).not.toBeInTheDocument(); // the form is on the other tabs

    await user.type(yaml, "x");
    expect(onText).toHaveBeenLastCalledWith(`${YAML}x`);
    await waitFor(() => expect(api.posted("/api/config/parse")).toEqual([{ text: YAML }, { text: `${YAML}x` }]));
  });

  it("shows form changes as text", async () => {
    formApi();
    const { user, onText } = await renderEditor();
    await user.click(within(option("translation.thinking")).getByRole("checkbox"));
    await waitFor(() => expect(onText).toHaveBeenCalled());
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByRole("textbox")).toHaveValue(JSON.stringify({ ollama: { host: HOST }, translation: { thinking: true } }));
  });
});

describe("Config editor: comments, and a job that has started", () => {
  const NOTICE = "Changing an option rewrites the YAML from the form; comments in the file are not kept.";

  it("warns on the form tabs that editing drops the file's comments", async () => {
    formApi();
    const { user } = await renderEditor({ hasComments: true });
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "All settings" }));
    expect(screen.getByText(NOTICE)).toBeInTheDocument();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument(); // editing the text keeps them
  });

  it("has no such warning for a file without comments or a read-only one", async () => {
    formApi();
    const { unmount } = render(<Harness onText={() => {}} />);
    await screen.findByDisplayValue(HOST);
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    unmount();

    await renderEditor({ hasComments: true, readOnly: true });
    expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
  });

  it("locks every control when read-only", async () => {
    const api = formApi();
    const { user } = await renderEditor({ readOnly: true });
    const toggle = within(option("translation.thinking")).getByRole("checkbox");
    expect(toggle).toBeDisabled();
    expect(within(option("ollama.model")).getByRole("combobox")).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Into language" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Swap the languages" })).toBeDisabled();
    await user.click(toggle);
    expect(written(api)).toEqual([]);

    await user.click(screen.getByRole("tab", { name: "All settings" }));
    expect(within(option("epub.chapter_title_template")).getByRole("textbox")).toBeDisabled();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByRole("textbox")).toHaveAttribute("readonly");
  });

  it("still lets a read-only config be browsed: the tabs and Collapse all / Expand all work", async () => {
    formApi();
    const { user } = await renderEditor({ readOnly: true });
    await user.click(screen.getByRole("button", { name: "Collapse all" }));
    expect(screen.queryByText("ollama.model")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Expand all" }));
    expect(screen.getByText("ollama.model")).toBeInTheDocument();
  });
});

describe("Config editor: when a change cannot be saved to the file", () => {
  it("undoes the change, says why, and leaves the YAML as it was", async () => {
    formApi({}, { "POST /api/config/dump": apiError("server restarting", 503) });
    const { user, onText } = await renderEditor();
    const thinking = within(option("translation.thinking")).getByRole("checkbox");
    await user.click(thinking);

    expect(await screen.findByText("The change was not written to the YAML, so it was undone: server restarting")).toBeInTheDocument();
    expect(thinking).not.toBeChecked(); // the form shows what the file says
    expect(group("Translation")).not.toHaveTextContent("changed");
    expect(onText).not.toHaveBeenCalled();
    await user.click(screen.getByRole("tab", { name: "YAML" }));
    expect(screen.getByRole("textbox")).toHaveValue(YAML);
  });

  it("drops the message once a later change goes through", async () => {
    let down = true;
    const api = formApi({}, {
      "POST /api/config/dump": (body: { values: Values }) => (down ? apiError("server restarting", 503) : { text: JSON.stringify(body.values) }),
    });
    const { user } = await renderEditor();
    const thinking = within(option("translation.thinking")).getByRole("checkbox");
    await user.click(thinking);
    await screen.findByText(/The change was not written to the YAML/);

    down = false;
    await user.click(thinking);
    await waitFor(() => expect(screen.queryByText(/The change was not written to the YAML/)).not.toBeInTheDocument());
    expect(thinking).toBeChecked();
    expect(lastWritten(api)).toEqual({ ollama: { host: HOST }, translation: { thinking: true } });
  });

  it("cannot be changed until the file has been read, so no change is made to settings it has not shown yet", async () => {
    const read = deferred<Response>();
    const api = formApi({}, { "POST /api/config/parse": () => read.promise });
    render(<Harness onText={vi.fn()} />);
    const from = await screen.findByRole("combobox", { name: "From language" });
    const thinking = within(option("translation.thinking")).getByRole("checkbox");
    await waitFor(() => expect(api.count("/api/config/parse")).toBe(1));
    expect(from).toBeDisabled();
    expect(thinking).toBeDisabled();

    read.resolve(jsonResponse({ values: { ollama: { host: HOST } }, syntax_error: null }));
    await screen.findByDisplayValue(HOST);
    expect(from).toBeEnabled();
    expect(thinking).toBeEnabled();
    expect(api.posted("/api/config/dump")).toEqual([]);
  });

  it("goes back to what the file says when two quick changes are both lost", async () => {
    // The first change is still being written when the second is made; the second cannot be written.
    const first = deferred<Response>();
    let writes = 0;
    formApi({}, {
      "POST /api/config/dump": () => {
        writes += 1;
        return writes === 1 ? first.promise : apiError("server restarting", 503);
      },
    });
    const { user, onText } = await renderEditor();
    const thinking = within(option("translation.thinking")).getByRole("checkbox");
    const conventions = within(option("consistency.conventions")).getByRole("checkbox");
    expect(conventions).toBeChecked(); // on unless the file says otherwise
    await user.click(thinking);
    await user.click(conventions);
    await screen.findByText(/The change was not written to the YAML/);
    first.resolve(jsonResponse({ text: "translation:\n  thinking: true\n" })); // too late: a newer change replaced it

    // Neither change is in the file, so neither is shown.
    await waitFor(() => expect(thinking).not.toBeChecked());
    expect(conventions).toBeChecked();
    expect(onText).not.toHaveBeenCalled();
  });
});

describe("Config editor: reading the config of a started job", () => {
  it("lets a read-only config be folded and unfolded group by group", async () => {
    formApi();
    const { user } = await renderEditor({ readOnly: true });
    await user.click(group("Translation"));
    expect(group("Translation")).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByText("translation.thinking")).not.toBeInTheDocument();

    await user.click(group("Translation"));
    expect(screen.getByText("translation.thinking")).toBeInTheDocument();
    expect(within(option("translation.thinking")).getByRole("checkbox")).toBeDisabled(); // still locked

    await user.click(screen.getByRole("tab", { name: "All settings" }));
    await user.click(group("EPUB"));
    expect(group("EPUB")).toHaveAttribute("aria-expanded", "false");
  });

  it("counts one setting as one setting", async () => {
    formApi();
    const { user } = await renderEditor();
    await user.click(screen.getByRole("tab", { name: "All settings" }));
    expect(group("EPUB")).toHaveTextContent(/1 setting$/);
    expect(group("Workflow")).toHaveTextContent(/2 settings$/);
  });
});
