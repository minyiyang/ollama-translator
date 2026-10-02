import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { mockApi } from "../test/mockApi";
import { ConfigEditor } from "./ConfigEditor";

// The Config tab for any language pair (docs/GENERIC_LANGUAGES.md, phase 4).

const field = (path: string, type: string, value: unknown) => ({
  path, key: path.split(".").at(-1), type, default: value, nullable: false,
});

const SCHEMA = {
  sections: [
    { key: "translation", title: "Translation", fields: [field("translation.direction", "string", "en-zh"), field("translation.style", "string", "literary")] },
    { key: "reprose", title: "Prose rewrite", fields: [field("reprose.enabled", "boolean", false)] },
    { key: "consistency", title: "Book consistency", fields: [field("consistency.conventions", "boolean", true)] },
  ],
};

function editorApi(direction: string) {
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
                { check: "punctuation conventions and character report", reason: "no house conventions for Japanese" },
              ]
            : [],
          notice: generic ? "Quality depends on the local model." : "",
        },
        error: "",
      };
    },
  });
}

describe("ConfigEditor languages", () => {
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
