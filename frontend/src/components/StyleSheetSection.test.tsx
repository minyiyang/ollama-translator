import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { StyleSheetSection, type StyleSheet } from "./StyleSheetSection";

const sheet: StyleSheet = {
  characters: [{ name: "Mouse", pronoun: "它", addressed_as: "您", voice: "formal", evidence: [], alternatives: ["他"] }],
  expressions: [{ source: "Off with her head!", rendering: "砍掉她的头！", note: "", evidence: [], alternatives: [] }],
  conventions: { quotation_marks: "“ ”", nested_quotation_marks: "‘ ’", ellipsis: "……", dash: "——", numerals: "" },
};

function renderSection(editable: boolean, onChange = vi.fn(), edited = false) {
  render(
    <StyleSheetSection
      sheet={sheet}
      editable={editable}
      edited={edited}
      pronouns={["他", "她", "它"]}
      addresses={["你", "您"]}
      onChange={onChange}
      onReset={vi.fn()}
    />,
  );
  return onChange;
}

describe("StyleSheetSection", () => {
  it("edits a character's pronoun and an expression's rendering", async () => {
    const user = userEvent.setup();
    const onChange = renderSection(true);
    expect(screen.getByText("also suggested: 他")).toBeInTheDocument();

    await user.selectOptions(screen.getByRole("combobox", { name: "Pronoun for Mouse" }), "他");
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      characters: [expect.objectContaining({ name: "Mouse", pronoun: "他", addressed_as: "您" })],
    }));

    await user.type(screen.getByRole("textbox", { name: "Rendering of Off with her head!" }), "!");
    expect(onChange.mock.lastCall?.[0].expressions[0].rendering).toBe("砍掉她的头！!");
  });

  it("removes an entry", async () => {
    const user = userEvent.setup();
    const onChange = renderSection(true);
    const table = screen.getByText("Off with her head!").closest("table") as HTMLElement;
    await user.click(within(table).getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ expressions: [] }));
  });

  it("is read-only after approval", () => {
    renderSection(false);
    expect(screen.queryByRole("combobox")).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
    expect(screen.getByText("砍掉她的头！")).toBeInTheDocument();
    expect(screen.getByText(/dash ——/)).toBeInTheDocument();
  });

  it("edits how a character is addressed and their voice", async () => {
    const user = userEvent.setup();
    const onChange = renderSection(true);
    const address = screen.getByRole("combobox", { name: "Address for Mouse" });
    expect(address).toHaveValue("您");
    expect(within(address).getAllByRole("option").map((o) => o.textContent)).toEqual(["—", "你", "您"]);

    await user.selectOptions(address, "你");
    expect(onChange.mock.lastCall?.[0].characters[0]).toMatchObject({ name: "Mouse", pronoun: "它", addressed_as: "你" });

    await user.selectOptions(screen.getByRole("combobox", { name: "Pronoun for Mouse" }), "—");
    expect(onChange.mock.lastCall?.[0].characters[0].pronoun).toBe("");

    await user.type(screen.getByRole("textbox", { name: "Voice of Mouse" }), "!");
    expect(onChange.mock.lastCall?.[0].characters[0].voice).toBe("formal!");
    expect(onChange.mock.lastCall?.[0].expressions).toEqual(sheet.expressions); // the rest of the sheet is untouched
  });

  it("removes a character, leaving the expressions alone", async () => {
    const user = userEvent.setup();
    const onChange = renderSection(true);
    const table = screen.getByText("Mouse").closest("table") as HTMLElement;
    await user.click(within(table).getByRole("button", { name: "Remove" }));
    expect(onChange).toHaveBeenLastCalledWith({ ...sheet, characters: [] });
  });

  it("offers to discard the edits only once the sheet has been edited", async () => {
    const onReset = vi.fn();
    const props = { sheet, editable: true, pronouns: [], addresses: [], onChange: vi.fn(), onReset };
    const { rerender } = render(<StyleSheetSection {...props} edited={false} />);
    expect(screen.queryByRole("button", { name: "Discard style-sheet edits" })).not.toBeInTheDocument();

    rerender(<StyleSheetSection {...props} edited />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Discard style-sheet edits" }));
    expect(onReset).toHaveBeenCalledOnce();

    rerender(<StyleSheetSection {...props} editable={false} edited />);
    expect(screen.queryByRole("button", { name: "Discard style-sheet edits" })).not.toBeInTheDocument();
  });

  it("says it is waiting for review only when the gate needs a person", () => {
    const props = { sheet, editable: true, edited: false, pronouns: [], addresses: [], onChange: vi.fn(), onReset: vi.fn() };
    const { rerender } = render(<StyleSheetSection {...props} />);
    expect(screen.queryByText(/Waiting for your review/)).not.toBeInTheDocument();

    rerender(<StyleSheetSection {...props} needsReview />);
    expect(screen.getByText(/Waiting for your review/)).toHaveTextContent("the style sheet is approved as shown here, whichever approve button you use.");
  });

  it("shows the approved values as text, with a dash for what is not set", () => {
    const approved: StyleSheet = {
      ...sheet,
      characters: [{ ...sheet.characters[0], pronoun: "", addressed_as: "", alternatives: [] }],
      expressions: [{ ...sheet.expressions[0], alternatives: ["砍她的头！", "斩首！"] }],
    };
    render(<StyleSheetSection sheet={approved} editable={false} edited={false} pronouns={[]} addresses={[]} onChange={vi.fn()} onReset={vi.fn()} />);
    const cells = within(screen.getByText("Mouse").closest("tr") as HTMLElement).getAllByRole("cell").map((cell) => cell.textContent);
    expect(cells).toEqual(["Mouse", "—", "—", "formal", ""]);
    expect(screen.getByText("also suggested: 砍她的头！ / 斩首！")).toBeInTheDocument();
    expect(screen.getByText(/^Conventions:/)).toHaveTextContent("Conventions: quotation marks “ ”, nested ‘ ’, ellipsis ……, dash ——");
  });

  it("says when the book has no characters or recurring expressions", () => {
    const empty: StyleSheet = { ...sheet, characters: [], expressions: [] };
    render(<StyleSheetSection sheet={empty} editable edited={false} pronouns={[]} addresses={[]} onChange={vi.fn()} onReset={vi.fn()} />);
    expect(screen.getByText("No entries were found.")).toBeInTheDocument();
    expect(screen.queryByRole("table")).not.toBeInTheDocument();
    expect(screen.getByText(/^Conventions:/)).toBeInTheDocument();
  });

  it("shows only the table that has entries", () => {
    const onlyExpressions: StyleSheet = { ...sheet, characters: [] };
    render(<StyleSheetSection sheet={onlyExpressions} editable edited={false} pronouns={[]} addresses={[]} onChange={vi.fn()} onReset={vi.fn()} />);
    expect(screen.getAllByRole("table")).toHaveLength(1);
    expect(screen.getByRole("columnheader", { name: "Recurring expression" })).toBeInTheDocument();
    expect(screen.queryByText("No entries were found.")).not.toBeInTheDocument();
  });
});
