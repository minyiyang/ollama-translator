import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import en from "./en.json";
import { direction, getLocale, initLocale, LOCALES, rich, setLocale, t, useT, type MessageKey } from "./index";

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

  it("show the key for a message that cannot be filled in, in any language", async () => {
    expect(t("server.error.job_exists")).toBe("server.error.job_exists");
    await setLocale("de");
    expect(t("server.error.job_exists")).toBe("server.error.job_exists");
    await setLocale("en");
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

  it("keeps the latest choice when an earlier one finishes loading afterwards", async () => {
    vi.resetModules();
    let arrive!: () => void;
    const held = new Promise<void>((resolve) => { arrive = resolve; });
    vi.doMock("./locales/fr.json", async () => {
      await held;
      return { default: { "shell.tab.glossary": "Glossaire" } };
    });
    try {
      const fresh = await import("./index");
      const slow = fresh.setLocale("fr");
      // Remembered before the catalog arrives: a reload now would start in French.
      expect(window.localStorage.getItem("ui-language")).toBe("fr");
      expect(fresh.getLocale()).toBe("en");
      await fresh.setLocale("en");
      arrive();
      await slow;
      expect(fresh.getLocale()).toBe("en");
      expect(document.documentElement.lang).toBe("en");
      expect(window.localStorage.getItem("ui-language")).toBe("en");
      // The catalog that arrived late is kept for the next time it is chosen.
      await fresh.setLocale("fr");
      expect(fresh.t("shell.tab.glossary")).toBe("Glossaire");
    } finally {
      vi.doUnmock("./locales/fr.json");
      vi.resetModules();
    }
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

describe("A layout-test language", () => {
  it("stretches and accents the text, keeping values and plural forms", async () => {
    await setLocale("en-XA", false);
    expect(t("shell.checksSkipped", { direction: "EN → JA", count: 1 })).toMatch(/^EN → JA · 1 çħḗçķ šķîƥƥḗḓ ~+$/);
    expect(t("shell.checksSkipped", { direction: "EN → JA", count: 3 })).toMatch(/^EN → JA · 3 çħḗçķš šķîƥƥḗḓ ~+$/);
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("writes right to left in Arabic letters, and turns the arrows", async () => {
    await setLocale("ar-XB", false);
    expect(document.documentElement.dir).toBe("rtl");
    expect(t("jobs.table.reviewGlossary")).toMatch(/^[^A-Za-z→]+ ← ~+$/);
    render(<p>{rich("jobs.new.dropHint", { kinds: "EPUB", browse: (chunks) => <button>{chunks}</button> })}</p>);
    expect(screen.getByRole("button")).not.toHaveTextContent(/[A-Za-z]/);

    await setLocale("fr", false);
    expect(document.documentElement.dir).toBe("ltr");
  });

  it("is not one of the languages on offer", () => {
    expect(LOCALES.map(({ code }) => code)).not.toContain("ar-XB");
    expect(direction("ar")).toBe("rtl");
    expect(direction("he-IL")).toBe("rtl");
    expect(direction("zh-CN")).toBe("ltr");
  });
});
