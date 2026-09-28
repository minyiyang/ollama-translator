import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { AppProviders } from "./App";
import { useDialog } from "./components/Dialog";
import { useToast } from "./components/Toast";

function ToastWithLink() {
  const toast = useToast();
  return <button onClick={() => toast("ok", <>Done. <Link to="/jobs/demo/progress">Follow progress</Link></>)}>notify</button>;
}

function DialogWithLink() {
  const ask = useDialog();
  return (
    <button onClick={() => ask("Title", <Link to="/series">Series</Link>, [{ value: "ok", label: "OK", primary: true }])}>
      ask
    </button>
  );
}

function Crash(): never {
  throw new Error("page exploded");
}

describe("AppProviders", () => {
  // Regression: toasts and dialogs used to sit outside the router, so a <Link> in
  // one crashed the whole app (a blank page after Recompile on the Text tab).
  it("renders a router link inside a toast", async () => {
    render(<AppProviders><ToastWithLink /></AppProviders>);
    await userEvent.setup().click(screen.getByRole("button", { name: "notify" }));
    expect(screen.getByRole("link", { name: "Follow progress" })).toHaveAttribute("href", "/jobs/demo/progress");
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("renders a router link inside a dialog", async () => {
    render(<AppProviders><DialogWithLink /></AppProviders>);
    await userEvent.setup().click(screen.getByRole("button", { name: "ask" }));
    const dialog = screen.getByRole("alertdialog");
    expect(within(dialog).getByRole("link", { name: "Series" })).toHaveAttribute("href", "/series");
  });

  it("shows the error banner instead of a blank page when a page throws", () => {
    vi.spyOn(console, "error").mockImplementation(() => {});
    render(<AppProviders><Crash /></AppProviders>);
    expect(screen.getByRole("alert")).toHaveTextContent("page exploded");
    expect(screen.getByRole("button", { name: "Reload" })).toBeInTheDocument();
  });
});
