import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { stageInfo } from "../lib/stageInfo";
import { StageTip } from "./StageTip";

const icon = () => screen.getByRole("button", { name: "About this stage" });

describe("The ⓘ next to a pipeline stage", () => {
  it("is not shown for a stage nobody has described", () => {
    const { container } = render(<StageTip stage="no_such_stage" result="Running." />);
    expect(container).toBeEmptyDOMElement();
  });

  it("opens when the pointer is over it and closes when the pointer leaves", async () => {
    const user = userEvent.setup();
    render(<StageTip stage="translate" result="Running." />);
    expect(icon()).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();

    await user.hover(icon());
    expect(icon()).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.unhover(icon());
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("describes the stage: what it does, input, output, checks, model, and the live result", async () => {
    render(<StageTip stage="translate" result="Running · 3/9 units." />);
    await userEvent.setup().hover(icon());

    const tip = screen.getByRole("tooltip");
    const info = stageInfo("translate")!;
    expect(tip).toHaveTextContent(info.does);
    for (const [term, text] of [["Input", info.input], ["Output", info.output], ["Checks", info.checks], ["Model", "ollama.model"], ["Result", "Running · 3/9 units."]]) {
      expect(within(tip).getByText(term).nextElementSibling).toHaveTextContent(text);
    }
    expect(within(tip).queryByText("Pauses")).not.toBeInTheDocument();
    // The icon is described by the card while it is open.
    expect(icon()).toHaveAttribute("aria-describedby", tip.id);
  });

  it("leaves out the model for a stage that calls none, and says where a gate pauses", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<StageTip stage="decompile" result="Completed." />);
    await user.hover(icon());
    expect(within(screen.getByRole("tooltip")).queryByText("Model")).not.toBeInTheDocument();
    unmount();

    render(<StageTip stage="compile" result="Paused." />);
    await user.hover(icon());
    expect(within(screen.getByRole("tooltip")).getByText("Pauses").nextElementSibling).toHaveTextContent(/Pauses for the Final review tab/);
  });

  it("opens when reached with Tab and closes when Tab moves on", async () => {
    const user = userEvent.setup();
    render(<StageTip stage="translate" result="Running." />);
    await user.tab();
    expect(icon()).toHaveFocus();
    expect(screen.getByRole("tooltip")).toBeInTheDocument();

    await user.tab();
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("stays open after a click, so it can be read, until it is clicked again", async () => {
    const user = userEvent.setup();
    render(<StageTip stage="translate" result="Running." />);

    await user.click(icon());
    await user.unhover(icon());
    expect(screen.getByRole("tooltip")).toBeInTheDocument(); // pinned

    await user.click(icon());
    await user.unhover(icon());
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });

  it("closes when the user clicks somewhere else", async () => {
    const user = userEvent.setup();
    render(<><StageTip stage="translate" result="Running." /><button>elsewhere</button></>);
    await user.click(icon());
    await user.click(screen.getByRole("button", { name: "elsewhere" }));
    expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
  });
});
