import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { CodeEditor, type SyntaxError_ } from "./CodeEditor";

const YAML = "ollama:\n  model: qwen3\ntranslation:\n  style: literary\n";

const syntaxError = (overrides: Partial<SyntaxError_> = {}): SyntaxError_ => ({
  message: "mapping values are not allowed here", line: 2, column: 9, context_line: null, ...overrides,
});

/** The editor is controlled; this owns the text the way ConfigEditor does. */
function Harness({ initial, onChange }: { initial: string; onChange: (value: string) => void }) {
  const [value, setValue] = useState(initial);
  return <CodeEditor value={value} error={null} onChange={(next) => { setValue(next); onChange(next); }} />;
}

const gutter = (container: HTMLElement) => [...(container.querySelector(".code-gutter > div") as HTMLElement).children].map((line) => line.textContent);

describe("The YAML editor", () => {
  it("shows the text with one line number per line", () => {
    const { container } = render(<CodeEditor value={YAML} onChange={() => {}} error={null} />);
    expect(screen.getByRole("textbox")).toHaveValue(YAML);
    expect(gutter(container)).toEqual(["1", "2", "3", "4", "5"]); // the trailing newline starts line 5
  });

  it("shows line 1 for an empty file", () => {
    const { container } = render(<CodeEditor value="" onChange={() => {}} error={null} />);
    expect(gutter(container)).toEqual(["1"]);
  });

  it("takes what is typed and numbers each new line", async () => {
    const onChange = vi.fn();
    const { container } = render(<Harness initial="a: 1" onChange={onChange} />);
    await userEvent.setup().type(screen.getByRole("textbox"), "{Enter}b: 2");
    expect(onChange).toHaveBeenLastCalledWith("a: 1\nb: 2");
    expect(gutter(container)).toEqual(["1", "2"]);
  });

  it("says the YAML is well-formed and that its values are checked on Validate", () => {
    render(<CodeEditor value={YAML} onChange={() => {}} error={null} />);
    expect(screen.getByText(/YAML syntax is valid\. Values are checked when you validate\./)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("textbox")).toHaveAttribute("aria-invalid", "false");
  });

  it("cannot be typed into for a job that has started, and does not mention Validate", async () => {
    const onChange = vi.fn();
    render(<CodeEditor value={YAML} onChange={onChange} error={null} readOnly />);
    const area = screen.getByRole("textbox");
    expect(area).toHaveAttribute("readonly");
    await userEvent.setup().type(area, "x");
    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByText(/YAML syntax is valid\./)).toBeInTheDocument();
    expect(screen.queryByText(/Values are checked when you validate/)).not.toBeInTheDocument();
  });

  it("says where a syntax error is and marks that line", () => {
    const { container } = render(<CodeEditor value={YAML} onChange={() => {}} error={syntaxError()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Syntax error on line 2, column 9: mapping values are not allowed here");
    expect(screen.getByRole("textbox")).toHaveAttribute("aria-invalid", "true");
    expect(gutter(container)).toEqual(["1", "▶ 2", "3", "4", "5"]);
    expect(screen.queryByText(/YAML syntax is valid/)).not.toBeInTheDocument();
  });

  it("also says where the broken block began", () => {
    render(<CodeEditor value={YAML} onChange={() => {}} error={syntaxError({ line: 4, column: null, context_line: 3 })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Syntax error on line 4: mapping values are not allowed here (the construct starts on line 3)");
  });

  it("reports an error it cannot place, without a jump button", () => {
    render(<CodeEditor value={YAML} onChange={() => {}} error={syntaxError({ line: null, column: null })} />);
    expect(screen.getByRole("alert")).toHaveTextContent("Syntax error: mapping values are not allowed here");
    expect(screen.queryByRole("button", { name: /Go to line/ })).not.toBeInTheDocument();
  });

  it("takes the user to the error: the cursor lands on that line with the line selected", async () => {
    render(<CodeEditor value={YAML} onChange={() => {}} error={syntaxError({ line: 3 })} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Go to line 3" }));

    const area = screen.getByRole("textbox") as HTMLTextAreaElement;
    expect(area).toHaveFocus();
    expect(area.value.slice(area.selectionStart, area.selectionEnd)).toBe("translation:");
  });
});
