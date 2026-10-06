import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/mockApi";
import { ConfigEditor } from "./ConfigEditor";

// The list of settings is fetched once per page load. These two tests share that
// one page load, in order: the first load fails, the second must ask again.

const routes = (schema: unknown) => ({
  "GET /api/config/schema": schema,
  "POST /api/config/parse": { values: {}, syntax_error: null },
  "POST /api/config/check": { errors: [] },
  "GET /api/models": { installed: [] },
  "GET /api/languages": { languages: [], support: null, error: "" },
});

const SCHEMA = {
  sections: [{
    key: "ollama", title: "Ollama",
    fields: [{ path: "ollama.model", key: "model", type: "string", default: "qwen3:14b", nullable: false }],
  }],
};

const editor = <ConfigEditor text={"ollama:\n  model: qwen3:14b\n"} onTextChange={() => {}} hasComments={false} />;

describe("Config editor: when the list of settings cannot be loaded", () => {
  it("says so instead of showing an empty form", async () => {
    mockApi(routes(apiError("server restarting", 503)));
    render(editor);
    expect(await screen.findByText("Could not load the list of settings from the server: server restarting. Reload the page to try again.")).toBeInTheDocument();
    expect(screen.queryByText("ollama.model")).not.toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "YAML" })).toBeEnabled(); // the text can still be edited
  });

  it("asks again the next time the editor opens, and shows the form", async () => {
    const api = mockApi(routes(SCHEMA));
    render(editor);
    expect(await screen.findByText("ollama.model")).toBeInTheDocument();
    expect(screen.queryByText(/Could not load the list of settings/)).not.toBeInTheDocument();
    expect(api.count("/api/config/schema")).toBe(1);
  });
});
