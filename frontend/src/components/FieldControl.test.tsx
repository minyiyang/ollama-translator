import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { SchemaField } from "../lib/configCatalog";
import { FieldControl } from "./FieldControl";

const field = (type: SchemaField["type"], overrides: Partial<SchemaField> = {}): SchemaField => ({
  path: "section.key", key: "key", type, default: null, nullable: false, ...overrides,
});

type Extra = { installed?: string[] | null; isModel?: boolean };

function renderControl(schema: SchemaField, value: unknown, extra: Extra = {}) {
  const onChange = vi.fn();
  const view = render(<FieldControl field={schema} value={value} onChange={onChange} {...extra} />);
  const rerender = (next: unknown) => view.rerender(<FieldControl field={schema} value={next} onChange={onChange} {...extra} />);
  return { onChange, user: userEvent.setup(), rerender };
}

describe("Changing one setting", () => {
  describe("an on/off setting", () => {
    it("is switched on with a click", async () => {
      const { onChange, user } = renderControl(field("boolean", { path: "reprose.enabled" }), false);
      const box = screen.getByRole("checkbox", { name: "reprose.enabled" });
      expect(box).not.toBeChecked();
      await user.click(box);
      expect(onChange).toHaveBeenCalledExactlyOnceWith(true);
    });

    it("shows as off when the config does not mention it", () => {
      renderControl(field("boolean"), undefined);
      expect(screen.getByRole("checkbox")).not.toBeChecked();
    });
  });

  describe("a setting with fixed choices", () => {
    const style = field("enum", { enum: ["literary", "plain", "custom"] });

    it("offers the allowed choices and takes the one picked", async () => {
      const { onChange, user } = renderControl(style, "plain");
      const select = screen.getByRole("combobox");
      expect(select).toHaveValue("plain");
      expect(within(select).getAllByRole("option").map((o) => o.textContent)).toEqual(["literary", "plain", "custom"]);

      await user.selectOptions(select, "custom");
      expect(onChange).toHaveBeenCalledExactlyOnceWith("custom");
    });

    it("can be set back to (none) when it is optional", async () => {
      const { onChange, user } = renderControl({ ...style, nullable: true }, "plain");
      await user.selectOptions(screen.getByRole("combobox"), "(none)");
      expect(onChange).toHaveBeenCalledExactlyOnceWith(null);
    });

    it("shows (none) for an optional setting nobody has set", () => {
      renderControl({ ...style, nullable: true }, null);
      expect(screen.getByRole("combobox")).toHaveValue("");
    });
  });

  describe("a number", () => {
    it("takes a whole number as it is typed", async () => {
      const { onChange, user } = renderControl(field("integer"), 8);
      const input = screen.getByRole("spinbutton");
      expect(input).toHaveValue(8);
      expect(input).toHaveAttribute("step", "1");

      await user.clear(input);
      await user.type(input, "42");
      expect(onChange.mock.calls.map(([value]) => value)).toEqual(["", 4, 42]);
      expect(input).toHaveValue(42);
    });

    it("takes a fraction where fractions are allowed", async () => {
      const { onChange, user } = renderControl(field("number"), null);
      const input = screen.getByRole("spinbutton");
      expect(input).toHaveAttribute("step", "any");
      await user.type(input, "0.25");
      expect(onChange).toHaveBeenLastCalledWith(0.25);
    });

    it("can be emptied when it is optional", async () => {
      const { onChange, user } = renderControl(field("integer", { nullable: true }), 8);
      await user.clear(screen.getByRole("spinbutton"));
      expect(onChange).toHaveBeenLastCalledWith(null);
    });

    it("says next to the box which values are allowed", () => {
      renderControl(field("number", { minimum: 0, exclusiveMaximum: 1 }), 0.5);
      expect(screen.getByText("≥ 0, < 1")).toBeInTheDocument();
    });

    it("says so for “more than” and “at most” limits too", () => {
      renderControl(field("integer", { exclusiveMinimum: 0, maximum: 64 }), 8);
      expect(screen.getByText("> 0, ≤ 64")).toBeInTheDocument();
    });

    it("shows the new value after Reset or an edit on the YAML tab", () => {
      const { rerender } = renderControl(field("integer"), 8);
      rerender(16);
      expect(screen.getByRole("spinbutton")).toHaveValue(16);
      rerender(null);
      expect(screen.getByRole("spinbutton")).toHaveValue(null);
    });

    it("does not fight the user while they are still typing", async () => {
      const { rerender, user } = renderControl(field("number"), 1);
      const input = screen.getByRole("spinbutton");
      await user.clear(input);
      rerender(""); // the parent echoes the emptied value back mid-edit
      await user.type(input, "7");
      rerender(7);
      expect(input).toHaveValue(7);
    });
  });

  describe("a line of text", () => {
    it("takes what is typed", async () => {
      const { onChange, user } = renderControl(field("string"), "http://localhost:11434");
      const input = screen.getByRole("textbox");
      expect(input).toHaveValue("http://localhost:11434");
      await user.type(input, "/");
      expect(onChange).toHaveBeenLastCalledWith("http://localhost:11434/");
    });

    it("can be emptied when it is optional, and then says (not set)", async () => {
      const { onChange, user, rerender } = renderControl(field("string", { nullable: true }), "x");
      await user.clear(screen.getByRole("textbox"));
      expect(onChange).toHaveBeenLastCalledWith(null);

      rerender(null);
      expect(screen.getByRole("textbox")).toHaveValue("");
      expect(screen.getByPlaceholderText("(not set)")).toBeInTheDocument();
    });

    it("can be emptied when it is required, for Validate to complain about", async () => {
      const { onChange, user } = renderControl(field("string"), "x");
      await user.clear(screen.getByRole("textbox"));
      expect(onChange).toHaveBeenLastCalledWith("");
    });
  });

  describe("the name of a model", () => {
    const installed = ["qwen3:14b", "gemma3:latest"];

    it("marks an installed model", () => {
      renderControl(field("string"), "qwen3:14b", { isModel: true, installed });
      expect(screen.getByTitle("Installed")).toHaveTextContent("✓");
      expect(screen.getByRole("combobox")).toHaveAttribute("list", "installed-models"); // suggestions come from Ollama
    });

    it("accepts a name without a tag when its :latest is installed", () => {
      renderControl(field("string"), "gemma3", { isModel: true, installed });
      expect(screen.getByTitle("Installed")).toBeInTheDocument();
    });

    it("marks a model Ollama does not have", () => {
      renderControl(field("string"), "qwen3:8b", { isModel: true, installed });
      expect(screen.getByTitle("Not installed in Ollama")).toHaveTextContent("✗ not installed");
    });

    it("is not fooled by a different size of the same model being installed", () => {
      renderControl(field("string"), "qwen3", { isModel: true, installed });
      expect(screen.getByTitle("Not installed in Ollama")).toBeInTheDocument();
    });

    it("says nothing while the model name is empty", () => {
      const { rerender } = renderControl(field("string"), "", { isModel: true, installed });
      expect(screen.queryByText(/✓|✗/)).not.toBeInTheDocument();
      rerender("qwen3:14b");
      expect(screen.getByTitle("Installed")).toBeInTheDocument();
    });

    it("says nothing when Ollama could not be asked", () => {
      renderControl(field("string"), "qwen3:14b", { isModel: true, installed: null });
      expect(screen.queryByText(/✓|✗/)).not.toBeInTheDocument();
    });

    it("has no model status on an ordinary text setting", () => {
      renderControl(field("string"), "qwen3:14b", { installed });
      expect(screen.queryByText(/✓|✗/)).not.toBeInTheDocument();
      expect(screen.getByRole("textbox")).not.toHaveAttribute("list");
    });
  });

  describe("a list", () => {
    const list = field("array", { item_type: "string" });

    it("shows each item and adds a trimmed one on Enter", async () => {
      const { onChange, user } = renderControl(list, ["seed.yaml"]);
      expect(screen.getByText("seed.yaml")).toBeInTheDocument();

      const input = screen.getByPlaceholderText("add item, Enter");
      await user.type(input, "  names.yaml {Enter}");
      expect(onChange).toHaveBeenCalledExactlyOnceWith(["seed.yaml", "names.yaml"]);
      expect(input).toHaveValue("");
    });

    it("adds what was typed when the field loses focus", async () => {
      const { onChange, user } = renderControl(list, []);
      await user.type(screen.getByPlaceholderText("add item, Enter"), "names.yaml");
      await user.tab();
      expect(onChange).toHaveBeenCalledExactlyOnceWith(["names.yaml"]);
    });

    it("ignores an empty or duplicate entry", async () => {
      const { onChange, user } = renderControl(list, ["seed.yaml"]);
      const input = screen.getByPlaceholderText("add item, Enter");
      await user.type(input, "   {Enter}");
      await user.type(input, "seed.yaml{Enter}");
      expect(onChange).not.toHaveBeenCalled();
      expect(input).toHaveValue("");
    });

    it("removes an item", async () => {
      const { onChange, user } = renderControl(list, ["seed.yaml", "names.yaml"]);
      await user.click(screen.getByRole("button", { name: "Remove seed.yaml" }));
      expect(onChange).toHaveBeenCalledExactlyOnceWith(["names.yaml"]);
    });

    it("shows an empty list when the config has something else there", () => {
      renderControl(list, "seed.yaml");
      expect(screen.queryByRole("button", { name: /^Remove/ })).not.toBeInTheDocument();
    });

    it("marks which listed models are installed", () => {
      renderControl(list, ["qwen3:14b", "phi4"], { isModel: true, installed: ["qwen3:14b"] });
      expect(screen.getByPlaceholderText("add model, Enter")).toHaveAttribute("list", "installed-models");
      expect(screen.getByText("qwen3:14b").closest(".tag")).toHaveTextContent("✓");
      expect(screen.getByText("phi4").closest(".tag")).toHaveTextContent("✗ not installed");
    });
  });

  describe("a table of names and values", () => {
    const caps = field("object", { key: "model_num_ctx", value_type: "integer" });
    const rowOf = (key: string) => screen.getByText(key).closest(".row") as HTMLElement;

    it("lists each name with its value and changes one", async () => {
      const { onChange, user } = renderControl(caps, { "qwen3:14b": 8192, phi4: 4096 });
      const input = within(rowOf("phi4")).getByRole("spinbutton");
      expect(input).toHaveValue(4096);

      await user.type(input, "0");
      expect(onChange).toHaveBeenLastCalledWith({ "qwen3:14b": 8192, phi4: 40960 });
    });

    it("removes a row", async () => {
      const { onChange, user } = renderControl(caps, { "qwen3:14b": 8192, phi4: 4096 });
      await user.click(within(rowOf("phi4")).getByRole("button", { name: "Remove" }));
      expect(onChange).toHaveBeenCalledExactlyOnceWith({ "qwen3:14b": 8192 });
    });

    it("adds a row for a model, suggested from the installed ones, starting at 0", async () => {
      const { onChange, user } = renderControl(caps, { phi4: 4096 });
      const key = screen.getByPlaceholderText("model name");
      expect(key).toHaveAttribute("list", "installed-models");
      const add = screen.getByRole("button", { name: "Add" });
      expect(add).toBeDisabled();

      await user.type(key, " gemma3 ");
      await user.click(add);
      expect(onChange).toHaveBeenCalledExactlyOnceWith({ phi4: 4096, gemma3: 0 });
      expect(key).toHaveValue("");
    });

    it("will not add a name that is already in the table", async () => {
      const { user } = renderControl(caps, { phi4: 4096 });
      await user.type(screen.getByPlaceholderText("model name"), "phi4");
      expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    });

    it("works the same for a table whose values are text", async () => {
      const labels = field("object", { key: "labels", value_type: "string" });
      const { onChange, user } = renderControl(labels, { person: "人名" });
      await user.type(within(rowOf("person")).getByRole("textbox"), "!");
      expect(onChange).toHaveBeenLastCalledWith({ person: "人名!" });

      await user.type(screen.getByPlaceholderText("key"), "place");
      await user.click(screen.getByRole("button", { name: "Add" }));
      expect(onChange).toHaveBeenLastCalledWith({ person: "人名", place: "" });
    });

    it("shows just the empty add row when the config has nothing there", () => {
      renderControl(caps, null);
      expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Add" })).toBeDisabled();
    });
  });
});
