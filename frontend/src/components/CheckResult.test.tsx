import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { LanguageSupport } from "../lib/languages";
import { CheckResult, type Check } from "./CheckResult";

// `summary` is a flat record of settings plus, optionally, the pair's language support.
const check = (overrides: Partial<Check> = {}, summary: Record<string, string | boolean | LanguageSupport> = {}): Check => ({
  ok: true,
  problems: [],
  job_id: "demo",
  models: [
    { role: "primary", model: "qwen3:14b", installed: true },
    { role: "audit", model: "gemma3:12b", installed: false },
    { role: "verifier", model: "phi4", installed: null },
  ],
  summary: {
    direction: "en-zh", style: "literary", glossary_review: "human",
    semantic_audit: true, reprose: false, final_review_required: false, ...summary,
  } as unknown as Check["summary"],
  stages: ["decompile", "translate", "custom_stage"],
  ...overrides,
});

const japanese: LanguageSupport = {
  pair: "en>ja",
  source: { code: "en", name: "English", tier: "tuned" },
  target: { code: "ja", name: "Japanese", tier: "generic" },
  skipped: [{ check: "prose rewrite", reason: "written for Chinese" }],
  notice: "Quality depends on the local model.",
};

describe("The result of Validate", () => {
  it("says a valid config is ready to start, naming the job", () => {
    render(<CheckResult check={check()} />);
    const banner = screen.getByText(/^Validated\./);
    expect(banner).toHaveTextContent("Validated. Use Start translation at the top to run demo.");
    expect(screen.queryByText(/Not ready yet/)).not.toBeInTheDocument();
  });

  it("lists every problem of an invalid config", () => {
    render(<CheckResult check={check({ ok: false, problems: ["source file is missing", "model qwen3 is not installed"] })} />);
    expect(screen.getByText(/Not ready yet/)).toBeInTheDocument();
    expect(screen.getAllByRole("listitem").map((li) => li.textContent)).toEqual(["source file is missing", "model qwen3 is not installed"]);
    expect(screen.queryByText(/^Validated\./)).not.toBeInTheDocument();
  });

  it("summarizes the settings the job would run with", () => {
    render(<CheckResult check={check()} />);
    expect(screen.getByText("EN → ZH")).toHaveAttribute("title", "en-zh");
    expect(screen.getByText("style: literary")).toBeInTheDocument();
    expect(screen.getByText("glossary review: human")).toBeInTheDocument();
    expect(screen.getByText("semantic audit: on")).toBeInTheDocument();
    expect(screen.getByText("prose rewrite: off")).toBeInTheDocument();
    expect(screen.getByText("final approval: when queue is not empty")).toBeInTheDocument();
  });

  it("says final approval is always required when the config asks for it", () => {
    render(<CheckResult check={check({}, { final_review_required: true, reprose: true, semantic_audit: false })} />);
    expect(screen.getByText("final approval: always")).toBeInTheDocument();
    expect(screen.getByText("prose rewrite: on")).toBeInTheDocument();
    expect(screen.getByText("semantic audit: off")).toBeInTheDocument();
  });

  it("marks each model installed, missing, or unknown", () => {
    render(<CheckResult check={check()} />);
    const cells = (role: string) => within(screen.getByRole("cell", { name: role }).closest("tr") as HTMLElement).getAllByRole("cell").map((c) => c.textContent);
    expect(cells("primary")).toEqual(["primary", "qwen3:14b", "✓"]);
    expect(cells("audit")).toEqual(["audit", "gemma3:12b", "✗ missing"]);
    expect(cells("verifier")).toEqual(["verifier", "phi4", "unknown"]);
  });

  it("lists the stages the book will go through, by name", () => {
    render(<CheckResult check={check()} />);
    expect(screen.getByText("Decompile source")).toBeInTheDocument();
    expect(screen.getByText("Translate")).toBeInTheDocument();
    expect(screen.getByText("custom_stage")).toBeInTheDocument();
  });

  it("spells out, already unfolded, which checks a less-supported language pair skips", () => {
    render(<CheckResult check={check({}, { direction: "en>ja", languages: japanese })} />);
    expect(screen.getByText("EN → JA")).toBeInTheDocument();
    expect(screen.getByText("Quality depends on the local model.")).toBeInTheDocument();
    expect(screen.getByText("ja: generic")).toBeInTheDocument();
    expect(screen.getByText("1 check skipped for this pair").closest("details")).toHaveAttribute("open");
    expect(screen.getByText("written for Chinese")).toBeInTheDocument();
  });

  it("keeps that list folded for English and Chinese", () => {
    const tuned: LanguageSupport = { ...japanese, pair: "zh-en", target: { code: "zh", name: "Simplified Chinese", tier: "tuned" }, notice: "" };
    render(<CheckResult check={check({}, { languages: tuned })} />);
    expect(screen.getByText("1 check skipped for this pair").closest("details")).not.toHaveAttribute("open");
  });

  it("says nothing about languages when every check runs", () => {
    const tuned: LanguageSupport = { ...japanese, target: { code: "zh", name: "Simplified Chinese", tier: "tuned" }, skipped: [], notice: "" };
    render(<CheckResult check={check({}, { languages: tuned })} />);
    expect(screen.queryByText(/English → /)).not.toBeInTheDocument();
    expect(screen.queryByText(/skipped for this pair/)).not.toBeInTheDocument();
  });
});
