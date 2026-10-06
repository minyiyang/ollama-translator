import { act, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ToastProvider, useToast } from "./Toast";

function Notifier({ content = "Saved.", kind = "ok", ms }: { content?: ReactNode; kind?: "ok" | "bad" | "warn" | "info"; ms?: number }) {
  const toast = useToast();
  return (
    <>
      <button onClick={() => toast(kind, content, ms)}>notify</button>
      <button onClick={() => toast("bad", "Second message.", 0)}>notify again</button>
      <button onClick={() => toast("ok", null)}>clear</button>
    </>
  );
}

const renderNotifier = (props: Parameters<typeof Notifier>[0] = {}) =>
  render(<ToastProvider><Notifier {...props} /></ToastProvider>);

afterEach(() => vi.useRealTimers());

describe("Notifications", () => {
  it("shows no message until something happens", () => {
    renderNotifier();
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("shows the message, coloured as a warning when it is one", async () => {
    renderNotifier({ kind: "warn", content: <>Careful: <b>read this</b></> });
    await userEvent.setup().click(screen.getByRole("button", { name: "notify" }));
    const toast = screen.getByRole("status");
    expect(toast).toHaveTextContent("Careful: read this");
    expect(toast).toHaveClass("warn");
  });

  it("shows only the latest message when a second one arrives", async () => {
    const user = userEvent.setup();
    renderNotifier();
    await user.click(screen.getByRole("button", { name: "notify" }));
    await user.click(screen.getByRole("button", { name: "notify again" }));
    expect(screen.getAllByRole("status")).toHaveLength(1);
    expect(screen.getByRole("status")).toHaveTextContent("Second message.");
    expect(screen.getByRole("status")).toHaveClass("bad");
  });

  it("goes away when the user clicks it", async () => {
    const user = userEvent.setup();
    renderNotifier();
    await user.click(screen.getByRole("button", { name: "notify" }));
    await user.click(screen.getByRole("status"));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("can be cleared by the page that raised it", async () => {
    const user = userEvent.setup();
    renderNotifier();
    await user.click(screen.getByRole("button", { name: "notify" }));
    await user.click(screen.getByRole("button", { name: "clear" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("goes away by itself after six seconds", () => {
    vi.useFakeTimers();
    renderNotifier();
    fireEvent.click(screen.getByRole("button", { name: "notify" }));
    act(() => { vi.advanceTimersByTime(5999); });
    expect(screen.getByRole("status")).toBeInTheDocument();
    act(() => { vi.advanceTimersByTime(1); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("goes away sooner when the page asks for a shorter time", () => {
    vi.useFakeTimers();
    renderNotifier({ ms: 1000 });
    fireEvent.click(screen.getByRole("button", { name: "notify" }));
    act(() => { vi.advanceTimersByTime(1000); });
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });

  it("stays until clicked when it reports an error the user must read", () => {
    vi.useFakeTimers();
    renderNotifier({ ms: 0 });
    fireEvent.click(screen.getByRole("button", { name: "notify" }));
    act(() => { vi.advanceTimersByTime(60_000); });
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("does not hide an error early because an older message was about to expire", () => {
    vi.useFakeTimers();
    renderNotifier();
    fireEvent.click(screen.getByRole("button", { name: "notify" }));
    act(() => { vi.advanceTimersByTime(5000); });
    fireEvent.click(screen.getByRole("button", { name: "notify again" })); // sticky
    act(() => { vi.advanceTimersByTime(10_000); });
    expect(screen.getByRole("status")).toHaveTextContent("Second message.");
  });
});

describe("Notifications outside the app shell", () => {
  it("raising a message does nothing and breaks nothing", async () => {
    render(<Notifier />);
    await userEvent.setup().click(screen.getByRole("button", { name: "notify" }));
    expect(screen.queryByRole("status")).not.toBeInTheDocument();
  });
});
