import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ErrorBoundary } from "./ErrorBoundary";

function Boom({ value }: { value: unknown }): never {
  throw value;
}

// React reports errors a boundary catches through console.error; keep test output quiet.
const silenceReactErrorLog = () => vi.spyOn(console, "error").mockImplementation(() => {});

describe("ErrorBoundary", () => {
  it("renders its children when nothing throws", () => {
    render(<ErrorBoundary><p>page content</p></ErrorBoundary>);
    expect(screen.getByText("page content")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("replaces a crashed page with the error, details, and a working Reload button", async () => {
    silenceReactErrorLog();
    const onReload = vi.fn();
    render(
      <ErrorBoundary onReload={onReload}>
        <Boom value={new TypeError("Cannot read properties of null")} />
      </ErrorBoundary>,
    );

    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Something went wrong while showing this page.");
    expect(alert).toHaveTextContent("Cannot read properties of null");
    expect(screen.getByText("Technical details")).toBeInTheDocument();

    await userEvent.setup().click(screen.getByRole("button", { name: "Reload" }));
    expect(onReload).toHaveBeenCalledOnce();
  });

  it("omits the details section when a thrown value has no stack", () => {
    silenceReactErrorLog();
    render(<ErrorBoundary><Boom value="plain failure" /></ErrorBoundary>);
    expect(screen.getByRole("alert")).toHaveTextContent("plain failure");
    expect(screen.queryByText("Technical details")).not.toBeInTheDocument();
  });
});
