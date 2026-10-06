import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { createRef } from "react";
import { describe, expect, it, vi } from "vitest";
import { SideItem, SideLayout } from "./SideLayout";

const KEY = "sidebar-collapsed:test";

function renderLayout(storageKey = KEY) {
  return render(
    <SideLayout sidebar={<button>Chapter One</button>} storageKey={storageKey} label="Chapters">
      <p>page body</p>
    </SideLayout>,
  );
}

const toggle = () => within(screen.getByRole("complementary", { name: "Chapters" })).getAllByRole("button")[0];

describe("A page with a sidebar", () => {
  it("shows the sidebar next to the page body", () => {
    renderLayout();
    const sidebar = screen.getByRole("complementary", { name: "Chapters" });
    expect(within(sidebar).getByRole("button", { name: "Chapter One" })).toBeInTheDocument();
    expect(within(screen.getByRole("main")).getByText("page body")).toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(toggle()).toHaveAttribute("title", "Hide chapters");
  });

  it("folds the sidebar away and brings it back, and remembers which the user chose", async () => {
    const user = userEvent.setup();
    renderLayout();

    await user.click(toggle());
    expect(screen.queryByRole("button", { name: "Chapter One" })).not.toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
    expect(toggle()).toHaveAttribute("title", "Show chapters");
    expect(screen.getByText("page body")).toBeInTheDocument();
    expect(localStorage.getItem(KEY)).toBe("1");

    await user.click(toggle());
    expect(screen.getByRole("button", { name: "Chapter One" })).toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "true");
    expect(localStorage.getItem(KEY)).toBe("0");
  });

  it("opens with the sidebar folded if the user left it folded last time", () => {
    localStorage.setItem(KEY, "1");
    renderLayout();
    expect(screen.queryByRole("button", { name: "Chapter One" })).not.toBeInTheDocument();
    expect(toggle()).toHaveAttribute("aria-expanded", "false");
  });

  it("folding the sidebar on one page leaves the other pages alone", () => {
    localStorage.setItem(KEY, "1");
    renderLayout("sidebar-collapsed:other");
    expect(screen.getByRole("button", { name: "Chapter One" })).toBeInTheDocument();
  });

  it("still folds in a browser that will not remember anything", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("denied"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("denied"); });
    renderLayout();
    expect(screen.getByRole("button", { name: "Chapter One" })).toBeInTheDocument();

    await userEvent.setup().click(toggle());
    expect(screen.queryByRole("button", { name: "Chapter One" })).not.toBeInTheDocument();
  });

  it("lets the page reach its sidebar, to scroll the current item into view", () => {
    const navRef = createRef<HTMLElement>();
    render(<SideLayout sidebar={null} storageKey={KEY} label="Segments" navRef={navRef}>body</SideLayout>);
    expect(navRef.current).toBe(screen.getByRole("complementary", { name: "Segments" }));
  });
});

describe("A sidebar entry", () => {
  it("shows its name, how many items it holds, and a line of detail, and can be clicked", async () => {
    const onClick = vi.fn();
    render(<SideItem onClick={onClick} count={3} sub="2 flagged">Chapter One</SideItem>);
    const item = screen.getByRole("button", { name: /Chapter One/ });
    expect(item).toHaveTextContent("Chapter One3");
    expect(item).toHaveTextContent("2 flagged");

    await userEvent.setup().click(item);
    expect(onClick).toHaveBeenCalledOnce();
  });

  it("says 0 when it holds nothing, and no number when counting makes no sense", () => {
    const { rerender } = render(<SideItem onClick={() => {}} count={0}>Dropped</SideItem>);
    expect(screen.getByRole("button")).toHaveTextContent("Dropped0");

    rerender(<SideItem onClick={() => {}}>Dropped</SideItem>);
    expect(screen.getByRole("button").textContent).toBe("Dropped");
  });
});
