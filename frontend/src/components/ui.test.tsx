import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { Bar, Card, Chip, Highlight, Segmented, Switch } from "./ui";

describe("A label chip", () => {
  it("shows its text, with more detail on hover when there is any", () => {
    render(<><Chip title="en-zh">EN → ZH</Chip><Chip>plain</Chip></>);
    expect(screen.getByText("EN → ZH")).toHaveAttribute("title", "en-zh");
    expect(screen.getByText("plain")).not.toHaveAttribute("title");
  });
});

describe("A progress bar", () => {
  const fill = (percent: number) => {
    const { container } = render(<Bar percent={percent} />);
    return (container.querySelector("i") as HTMLElement).style.width;
  };

  it("is filled as far as the work is done", () => {
    expect(fill(40)).toBe("40%");
  });

  it("is never less than empty or more than full", () => {
    expect(fill(-20)).toBe("0%");
    expect(fill(250)).toBe("100%");
  });

  it("is empty when there is nothing to measure against", () => {
    expect(fill(0 / 0)).toBe("0%"); // 0 of 0 stages
  });
});

describe("A card", () => {
  it("has a heading only when it has a title", () => {
    const { rerender } = render(<Card title="Pipeline"><p>body</p></Card>);
    expect(screen.getByRole("heading", { name: "Pipeline" })).toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();

    rerender(<Card><p>body</p></Card>);
    expect(screen.queryByRole("heading")).not.toBeInTheDocument();
    expect(screen.getByText("body")).toBeInTheDocument();
  });
});

describe("An on/off switch", () => {
  it("turns on with one click and off with the next", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    const { rerender } = render(<Switch checked={false} onChange={onChange} label="reprose.enabled" />);
    const box = screen.getByRole("checkbox", { name: "reprose.enabled" });
    expect(box).not.toBeChecked();

    await user.click(box);
    expect(onChange).toHaveBeenLastCalledWith(true);

    rerender(<Switch checked onChange={onChange} label="reprose.enabled" />);
    expect(box).toBeChecked();
    await user.click(box);
    expect(onChange).toHaveBeenLastCalledWith(false);
  });
});

describe("A row of choices", () => {
  const options = [["llm", "LLM reviews"], ["human", "I review"], ["none", "No review"]] as const;

  it("shows which choice is current and takes the one the user clicks", async () => {
    const onChange = vi.fn();
    render(<Segmented value="human" options={options} onChange={onChange} />);
    const radios = screen.getAllByRole("radio");
    expect(radios.map((r) => r.textContent)).toEqual(["LLM reviews", "I review", "No review"]);
    expect(screen.getByRole("radio", { name: "I review" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "LLM reviews" })).not.toBeChecked();

    await userEvent.setup().click(screen.getByRole("radio", { name: "No review" }));
    expect(onChange).toHaveBeenCalledExactlyOnceWith("none");
  });
});

describe("A quoted phrase in a passage", () => {
  const marked = (text: string, quote?: string) => {
    const { container } = render(<p><Highlight text={text} quote={quote} /></p>);
    return { text: container.textContent, marks: [...container.querySelectorAll("mark")].map((m) => m.textContent) };
  };

  it("is highlighted where it first appears, whatever its capitals, without changing the passage", () => {
    expect(marked("Alice met alice again.", "ALICE")).toEqual({ text: "Alice met alice again.", marks: ["Alice"] });
  });

  it("leaves the passage plain when there is nothing to find or it is not there", () => {
    expect(marked("Alice met the Mouse.")).toEqual({ text: "Alice met the Mouse.", marks: [] });
    expect(marked("Alice met the Mouse.", "")).toEqual({ text: "Alice met the Mouse.", marks: [] });
    expect(marked("Alice met the Mouse.", "Rabbit")).toEqual({ text: "Alice met the Mouse.", marks: [] });
  });
});
