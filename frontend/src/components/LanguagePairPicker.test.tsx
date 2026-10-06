import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { hiddenOptions, pairCodes, pairOf, type LanguageSupport } from "../lib/languages";
import { jsonResponse, mockApi } from "../test/mockApi";
import { LanguageNotes, LanguagePairPicker } from "./LanguagePairPicker";

// Language selectors for any pair (docs/GENERIC_LANGUAGES.md, phase 4).

const LANGUAGES = [
  { code: "en", name: "English", tier: "tuned" },
  { code: "zh", name: "Simplified Chinese", tier: "tuned" },
  { code: "ja", name: "Japanese", tier: "generic" },
];

const support = (pair: string): LanguageSupport | null => {
  const [source, target] = pairCodes(pair);
  const side = (code: string) => LANGUAGES.find((l) => l.code === code) as LanguageSupport["source"];
  if (!side(source) || !side(target)) return null;
  const generic = side(source).tier !== "tuned" || side(target).tier !== "tuned";
  return {
    pair,
    source: side(source),
    target: side(target),
    skipped: generic ? [{ check: "prose rewrite", reason: "its prompt and rules are written for Chinese; reprose stays off" }] : [],
    notice: generic ? "Quality depends on the local model." : "",
  };
};

function languagesApi() {
  return mockApi({
    "GET /api/languages": (_: unknown, url: URL) => ({
      languages: LANGUAGES,
      support: support(url.searchParams.get("pair") ?? ""),
      error: "",
    }),
  });
}

describe("pair helpers", () => {
  it("keeps the two legacy spellings and writes every other pair with an arrow", () => {
    expect(pairOf("en", "zh")).toBe("en-zh");
    expect(pairOf("zh", "en")).toBe("zh-en");
    expect(pairOf("en", "ja")).toBe("en>ja");
    expect(pairCodes("pt-BR>ja")).toEqual(["pt-BR", "ja"]);
    expect(pairCodes("zh-en")).toEqual(["zh", "en"]);
  });

  it("hides the options of a skipped check", () => {
    const hidden = hiddenOptions(support("en>ja"));
    expect([...hidden.keys()]).toContain("reprose.enabled");
    expect(hiddenOptions(support("en-zh")).size).toBe(0);
  });
});

describe("LanguagePairPicker", () => {
  it("writes the pair when a listed language is picked, and swaps", async () => {
    languagesApi();
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<LanguagePairPicker value="en-zh" onChange={onChange} />);
    expect(await screen.findByText("Simplified Chinese")).toBeTruthy();
    const target = screen.getByRole("combobox", { name: "Into language" });
    await user.clear(target);
    await user.type(target, "ja");
    expect(onChange).toHaveBeenLastCalledWith("en>ja");
    await user.click(screen.getByRole("button", { name: "Swap the languages" }));
    expect(onChange).toHaveBeenLastCalledWith("ja>en");
  });

  it("accepts a code that is not listed once typed in full", async () => {
    languagesApi();
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<LanguagePairPicker value="en-zh" onChange={onChange} />);
    const target = await screen.findByRole("combobox", { name: "Into language" });
    await user.clear(target);
    await user.type(target, "pt-BR{Enter}");
    expect(onChange).toHaveBeenLastCalledWith("en>pt-BR");
  });

  it("states the tier, the model notice, and the skipped checks for a generic pair", async () => {
    languagesApi();
    render(<LanguagePairPicker value="en>ja" onChange={() => {}} />);
    expect(await screen.findByText("Quality depends on the local model.")).toBeTruthy();
    expect(screen.getByText("ja: generic")).toBeTruthy();
    expect(screen.getByText("1 check skipped for this pair")).toBeTruthy();
  });

  it("says nothing extra for a tuned pair", async () => {
    languagesApi();
    render(<LanguagePairPicker value="en-zh" onChange={() => {}} />);
    await screen.findByText("Simplified Chinese");
    expect(screen.queryByText(/skipped for this pair/)).toBeNull();
  });

  it("names each listed language under its code and offers the list as suggestions", async () => {
    languagesApi();
    render(<LanguagePairPicker value="en>ja" onChange={() => {}} />);
    expect(await screen.findByText("Japanese")).toBeInTheDocument();
    expect(screen.getByText("English")).toBeInTheDocument();
    const options = [...document.querySelectorAll("#language-codes option")].map((o) => [o.getAttribute("value"), o.getAttribute("label")]);
    expect(options).toEqual([["en", "English · tuned"], ["zh", "Simplified Chinese · tuned"], ["ja", "Japanese"]]);
  });

  it("waits for Enter or blur before writing a code that is not in the list", async () => {
    languagesApi();
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<LanguagePairPicker value="en-zh" onChange={onChange} />);
    const source = await screen.findByRole("combobox", { name: "From language" });
    await screen.findByText("Simplified Chinese");

    await user.clear(source);
    await user.type(source, "pt-BR");
    expect(onChange).not.toHaveBeenCalled(); // "pt", "pt-B" … are not pairs worth writing
    await user.tab();
    expect(onChange).toHaveBeenCalledExactlyOnceWith("pt-BR>zh");
  });

  it("writes nothing while a side is empty or when the pair is unchanged", async () => {
    languagesApi();
    const onChange = vi.fn();
    const user = userEvent.setup();
    render(<LanguagePairPicker value="en-zh" onChange={onChange} />);
    const target = await screen.findByRole("combobox", { name: "Into language" });
    await screen.findByText("Simplified Chinese");

    await user.clear(target);
    await user.tab();
    expect(onChange).not.toHaveBeenCalled();

    await user.type(target, "zh{Enter}");
    expect(onChange).not.toHaveBeenCalled(); // back to the pair it already has
  });

  it("follows a pair changed from outside", async () => {
    languagesApi();
    const { rerender } = render(<LanguagePairPicker value="en-zh" onChange={() => {}} />);
    await screen.findByText("Simplified Chinese");
    rerender(<LanguagePairPicker value="zh-en" onChange={() => {}} />);
    expect(screen.getByRole("combobox", { name: "From language" })).toHaveValue("zh");
    expect(screen.getByRole("combobox", { name: "Into language" })).toHaveValue("en");
  });

  it("locks both sides and the swap when disabled", async () => {
    languagesApi();
    render(<LanguagePairPicker value="en-zh" onChange={() => {}} disabled />);
    await screen.findByText("Simplified Chinese");
    expect(screen.getByRole("combobox", { name: "From language" })).toBeDisabled();
    expect(screen.getByRole("combobox", { name: "Into language" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Swap the languages" })).toBeDisabled();
  });

  it("keeps the notes to itself when asked to", async () => {
    languagesApi();
    render(<LanguagePairPicker value="en>ja" onChange={() => {}} showNotes={false} />);
    await screen.findByText("Japanese");
    expect(screen.queryByText("Quality depends on the local model.")).not.toBeInTheDocument();
    expect(screen.queryByText(/skipped for this pair/)).not.toBeInTheDocument();
  });

  it("shows the server's reason when the value is not a pair, instead of the notes", async () => {
    mockApi({ "GET /api/languages": { languages: LANGUAGES, support: support("en>ja"), error: "'en>en' translates a language into itself" } });
    render(<LanguagePairPicker value="en>en" onChange={() => {}} />);
    expect(await screen.findByText("'en>en' translates a language into itself")).toBeInTheDocument();
    expect(screen.queryByText("Quality depends on the local model.")).not.toBeInTheDocument();
  });

  it("shows why the languages could not be loaded", async () => {
    mockApi({ "GET /api/languages": jsonResponse({ error: "server restarting" }, 503) });
    render(<LanguagePairPicker value="en>ko" onChange={() => {}} />);
    expect(await screen.findByText("server restarting")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Into language" })).toHaveValue("ko"); // still editable
  });
});

describe("LanguageNotes", () => {
  it("lists the skipped checks with their reasons, folded unless asked open", () => {
    const pair = support("en>ja") as LanguageSupport;
    const { rerender } = render(<LanguageNotes support={pair} />);
    expect(screen.getByText("English → Japanese")).toBeInTheDocument();
    expect(screen.getByText("en: tuned")).toHaveAttribute("title", "Full profile, benchmarked, prompts with examples.");
    expect(screen.getByText("ja: generic")).toHaveAttribute("title", expect.stringContaining("No profile yet"));
    const details = screen.getByText("1 check skipped for this pair").closest("details") as HTMLElement;
    expect(details).not.toHaveAttribute("open");
    expect(details).toHaveTextContent("prose rewrite: its prompt and rules are written for Chinese; reprose stays off");

    rerender(<LanguageNotes support={pair} open />);
    expect(details).toHaveAttribute("open");
  });

  it("counts several skipped checks, and shows no list or notice when there are none", () => {
    const pair = support("en>ja") as LanguageSupport;
    const { rerender } = render(<LanguageNotes support={{ ...pair, skipped: [...pair.skipped, { check: "punctuation conventions", reason: "no house conventions" }] }} />);
    expect(screen.getByText("2 checks skipped for this pair")).toBeInTheDocument();

    rerender(<LanguageNotes support={{ ...pair, skipped: [], notice: "" }} />);
    expect(screen.queryByText(/skipped for this pair/)).not.toBeInTheDocument();
    expect(screen.queryByText("Quality depends on the local model.")).not.toBeInTheDocument();
  });
});
