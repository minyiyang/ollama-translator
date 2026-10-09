import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { ConfigEditor } from "./ConfigEditor";

// A book and a subtitle job share one config; the form shows each only what it reads.

const field = (path: string, type: string, value: unknown, extra: Record<string, unknown> = {}) => ({
  path, key: path.split(".").at(-1), type, default: value, nullable: false, ...extra,
});

const FORMATS = ["source", "epub", "txt", "md", "html", "docx", "pdf", "srt", "vtt", "ass"];
const SCHEMA = {
  sections: [
    {
      key: "translation", title: "Translation", fields: [
        field("translation.direction", "string", "en-zh"),
        field("translation.style", "string", "literary"),
        field("translation.translated_title", "string", null, { nullable: true }),
      ],
    },
    { key: "reprose", title: "Prose rewrite", fields: [field("reprose.enabled", "boolean", false), field("reprose.model", "string", "")] },
    { key: "epub", title: "EPUB", fields: [field("epub.strip_print_page_markers", "boolean", true)] },
    {
      key: "output", title: "Output format", fields: [
        field("output.format", "enum", "source", { enum: FORMATS }),
        field("output.pdf_font", "string", null, { nullable: true }),
      ],
    },
    { key: "subtitles", title: "Subtitles", fields: [field("subtitles.line_characters", "integer", null, { nullable: true })] },
    { key: "glossary", title: "Glossary", fields: [field("glossary.extraction_enabled", "boolean", true)] },
    {
      key: "consistency", title: "Content consistency", fields: [
        field("consistency.enabled", "boolean", true),
        field("consistency.quoted_speech", "boolean", true),
        field("consistency.story_context.enabled", "boolean", false),
        field("consistency.story_context.chapters_before", "integer", 2),
      ],
    },
  ],
};

function editorApi(values: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/config/schema": SCHEMA,
    "POST /api/config/parse": { values: { translation: { direction: "en-zh" }, ...values }, syntax_error: null },
    "POST /api/config/check": { errors: [] },
    "GET /api/models": { installed: [] },
    "GET /api/languages": {
      languages: [],
      support: {
        pair: "en-zh",
        source: { code: "en", name: "English", tier: "tuned" },
        target: { code: "zh", name: "Simplified Chinese", tier: "tuned" },
        skipped: [],
        notice: "",
      },
      error: "",
    },
  });
}

async function renderEditor(jobType?: "book" | "subtitles") {
  render(<ConfigEditor text={"translation:\n  direction: en-zh\n"} onTextChange={() => {}} hasComments={false} jobType={jobType} />);
  await screen.findByText("output.format", { selector: ".path" });
}

const paths = () => [...document.querySelectorAll(".opt .path")].map((node) => node.textContent);
const groups = () => screen.getAllByRole("button", { expanded: true }).map((button) => button.querySelector(".group-title")?.textContent);
const formats = () =>
  within(screen.getByText("output.format", { selector: ".path" }).closest(".opt") as HTMLElement)
    .getAllByRole("option").map((option) => option.textContent);

describe("Config editor: the settings a kind of job reads", () => {
  it("shows a subtitle job its reading limits and none of a book's settings", async () => {
    editorApi();
    await renderEditor("subtitles");
    expect(paths()).toContain("subtitles.line_characters");
    for (const path of ["reprose.enabled", "reprose.model", "epub.strip_print_page_markers", "output.pdf_font", "translation.translated_title"]) {
      expect(paths()).not.toContain(path);
    }
    // A group with nothing left in it is gone, not left empty.
    expect(groups()).toContain("Subtitles");
    expect(groups()).not.toContain("Book title");
    expect(screen.getByText(/Settings only a book uses .* are not shown for a subtitle job\. The YAML tab has every setting\./)).toBeInTheDocument();
    // Its output is a subtitle file: the format it came in, or another subtitle format.
    expect(formats()).toEqual(["source", "srt", "vtt", "ass"]);
    expect(screen.getByText(/The subtitle format the finished file is given back in\./)).toBeInTheDocument();
  });

  it("shows a book its settings and none of a subtitle job's", async () => {
    editorApi();
    await renderEditor("book");
    expect(paths()).toEqual(expect.arrayContaining(["reprose.enabled", "epub.strip_print_page_markers", "output.pdf_font", "translation.translated_title"]));
    expect(paths()).not.toContain("subtitles.line_characters");
    expect(groups()).toContain("Book title");
    expect(groups()).not.toContain("Subtitles");
    expect(screen.getByText(/Settings only a subtitle job uses \(reading limits\) are not shown for a book\./)).toBeInTheDocument();
    expect(formats()).toEqual(["source", "epub", "txt", "md", "html", "docx", "pdf"]);
    expect(screen.getByText(/The format the finished book is given back in\./)).toBeInTheDocument();
  });

  it("words a subtitle job's settings for subtitles: the film and its parts, not a book and its chapters", async () => {
    editorApi();
    await renderEditor("subtitles");
    const named = (path: string) =>
      (screen.getByText(path, { selector: ".path" }).closest(".opt") as HTMLElement).querySelector(".name")?.textContent;
    expect(named("glossary.extraction_enabled")).toBe("Extract terms from the subtitles");
    expect(named("consistency.story_context.chapters_before")).toBe("Earlier parts in context");
    expect(screen.getByText("How many previous part summaries each chunk sees.")).toBeInTheDocument();
    expect(screen.getByText(/^Summarize each part of the film first/)).toBeInTheDocument();
    // The check of lines inside quotation marks finds nothing in subtitles, and is not offered.
    expect(paths()).toContain("consistency.enabled");
    expect(paths()).not.toContain("consistency.quoted_speech");
    expect(groups()).toContain("Content consistency");
    // A setting's path is its name in the file and stays as it is; what is said about it is the subtitles' wording.
    const said = [...document.querySelectorAll(".opt .name, .opt .help")].map((node) => node.textContent).join(" ");
    expect(said).not.toMatch(/chapter|\bbook\b/i);
  });

  it("keeps a book's settings worded for a book", async () => {
    editorApi();
    await renderEditor("book");
    expect(screen.getByText("Extract terms from the book")).toBeInTheDocument();
    expect(screen.getByText("Earlier chapters in context")).toBeInTheDocument();
    expect(paths()).toContain("consistency.quoted_speech");
  });

  it("shows everything where the kind of job is not known", async () => {
    editorApi();
    await renderEditor();
    expect(paths()).toEqual(expect.arrayContaining(["reprose.enabled", "subtitles.line_characters", "output.pdf_font"]));
    expect(formats()).toEqual(FORMATS);
    expect(screen.queryByText(/are not shown for/)).not.toBeInTheDocument();
  });

  it("keeps a format the file already names in the menu, so that it can be seen and changed", async () => {
    editorApi({ output: { format: "docx" } });
    await renderEditor("subtitles");
    // The file's values reach the form a moment after it is drawn.
    await waitFor(() => expect(formats()).toEqual(["source", "srt", "vtt", "ass", "docx"]));
  });

  it("leaves the other kind's settings out of All settings too, with their sections", async () => {
    editorApi();
    await renderEditor("subtitles");
    await userEvent.click(screen.getByRole("tab", { name: "All settings" }));
    expect(paths()).toEqual(expect.arrayContaining(["subtitles.line_characters", "output.format", "translation.style"]));
    expect(paths()).not.toEqual(expect.arrayContaining(["reprose.enabled"]));
    expect(paths()).not.toContain("epub.strip_print_page_markers");
    expect(paths()).not.toContain("output.pdf_font");
    const jumps = [...document.querySelectorAll(".section-nav button")].map((button) => button.textContent);
    expect(jumps).toEqual(["Translation", "Output format", "Subtitles", "Glossary", "Content consistency"]);
    expect(paths()).not.toContain("consistency.quoted_speech");
  });
});
