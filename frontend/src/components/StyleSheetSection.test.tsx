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
});
