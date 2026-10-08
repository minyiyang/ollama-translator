import { act, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import en from "./en.json";
import { getLocale, initLocale, LOCALES, rich, setLocale, t, useT, type MessageKey } from "./index";

describe("Messages", () => {
  it("fill in values and pick the plural form", () => {
    expect(t("shell.checksSkipped", { direction: "EN → JA", count: 1 })).toBe("EN → JA · 1 check skipped");
    expect(t("shell.checksSkipped", { direction: "EN → JA", count: 3 })).toBe("EN → JA · 3 checks skipped");
  });

  it("wrap tagged text in the element the caller gives", () => {
    render(<p>{rich("jobs.new.dropHint", { kinds: "a book", browse: (chunks) => <button>{chunks}</button> })}</p>);
    expect(screen.getByRole("button", { name: "Browse…" })).toBeInTheDocument();
    expect(screen.getByText(/^Drop a book here, or/)).toBeInTheDocument();
  });

  it("show the key for a message the catalog lacks", () => {
    expect(t("no.such.message" as MessageKey)).toBe("no.such.message");
  });
});

describe("The interface language", () => {
  it("is English until one is chosen", () => {
    expect(getLocale()).toBe("en");
    expect(document.documentElement.lang).toBe("en");
  });

  it.each(LOCALES.filter(({ code }) => code !== "en"))("switches to $name", async ({ code }) => {
    await setLocale(code);
    expect(getLocale()).toBe(code);
    expect(document.documentElement.lang).toBe(code);
    expect(window.localStorage.getItem("ui-language")).toBe(code);
    expect(t("shell.tab.glossary")).not.toBe(en["shell.tab.glossary"]);
    // Values still land in the translated sentence.
    expect(t("shell.seriesChip", { name: "Qel", version: "v2" })).toMatch(/Qel.*v2/);
  });

  it("re-renders a component that translates", async () => {
    function Tab() {
      return <p data-testid="tab">{useT()("shell.tab.glossary")}</p>;
    }
    render(<Tab />);
    expect(screen.getByTestId("tab")).toHaveTextContent("Glossary");
    await act(() => setLocale("zh-CN"));
    expect(screen.getByTestId("tab")).toHaveTextContent("术语表");
  });

  it("starts in the saved language", async () => {
    window.localStorage.setItem("ui-language", "zh-CN");
    await initLocale();
    expect(getLocale()).toBe("zh-CN");
  });

  it("starts in English when nothing usable is saved", async () => {
    window.localStorage.setItem("ui-language", "tlh");
    await initLocale();
    expect(getLocale()).toBe("en");
  });
});
