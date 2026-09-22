import { describe, expect, it } from "vitest";
import { glossaryReviewMode, GLOSSARY_REVIEW_FLAGS } from "./configCatalog";
import { getPath, hasPath, setPath, unsetPath } from "./configValues";
import { diffChars } from "./diff";
import { duration } from "./format";

describe("configValues", () => {
  it("sets nested paths without mutating the input", () => {
    const base = { ollama: { model: "a" } };
    const next = setPath(base, "audit.quantity.enabled", true);
    expect(next).toEqual({ ollama: { model: "a" }, audit: { quantity: { enabled: true } } });
    expect(base).toEqual({ ollama: { model: "a" } });
    expect(getPath(next, "audit.quantity.enabled")).toBe(true);
    expect(hasPath(next, "audit.quantity.model")).toBe(false);
  });

  it("drops emptied sections when unsetting", () => {
    const values = { audit: { quantity: { enabled: true } }, ollama: { model: "a", host: "h" } };
    expect(unsetPath(values, "audit.quantity.enabled")).toEqual({ ollama: { model: "a", host: "h" } });
    expect(unsetPath(values, "ollama.host")).toEqual({ audit: { quantity: { enabled: true } }, ollama: { model: "a" } });
  });
});

describe("glossary review mode", () => {
  it("round-trips the two workflow flags", () => {
    for (const mode of ["llm", "human", "none"] as const) {
      const { require, llm } = GLOSSARY_REVIEW_FLAGS[mode];
      expect(glossaryReviewMode(require, llm)).toBe(mode);
    }
  });
});

describe("diffChars", () => {
  it("marks only the changed CJK characters", () => {
    expect(diffChars("我记不全了", "我连一半都记不住")).toEqual([
      { kind: "same", text: "我" },
      { kind: "ins", text: "连一半都" },
      { kind: "same", text: "记不" },
      { kind: "del", text: "全了" },
      { kind: "ins", text: "住" },
    ]);
  });
  it("returns one unchanged part for identical text", () => {
    expect(diffChars("相同", "相同")).toEqual([{ kind: "same", text: "相同" }]);
  });
});

describe("duration", () => {
  it("formats seconds, minutes and hours", () => {
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe("45s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:11:39Z")).toBe("11m 39s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T02:30:00Z")).toBe("2h 30m");
  });
});

describe("roughDuration", () => {
  it("rounds to readable units", async () => {
    const { roughDuration } = await import("./format");
    expect(roughDuration(0.4)).toBe("1 s");
    expect(roughDuration(45)).toBe("45 s");
    expect(roughDuration(719)).toBe("12 min");
    expect(roughDuration(4778)).toBe("1 h 20 min");
    expect(roughDuration(7200)).toBe("2 h");
  });
});

describe("stage descriptions", () => {
  it("cover every pipeline stage with the required parts", async () => {
    const { STAGE_LABELS } = await import("./stages");
    const { STAGE_INFO } = await import("./stageInfo");
    for (const stage of Object.keys(STAGE_LABELS)) {
      const info = STAGE_INFO[stage];
      expect(info, stage).toBeDefined();
      for (const part of ["does", "input", "output", "checks"] as const) expect(info[part].length, `${stage}.${part}`).toBeGreaterThan(10);
    }
  });
});

describe("tabStates", () => {
  const st = (name: string, status: string) => ({ name, status, attempts: 0, message: "" });
  const pipeline = (overrides: Record<string, string>) =>
    ["decompile", "extract_glossary", "resolve_glossary", "approve_glossary", "translate", "compile", "validate_epub"].map((n) =>
      st(n, overrides[n] ?? "pending"));
  it("marks the tab that holds the current stage", async () => {
    const { tabStates } = await import("./stages");
    expect(tabStates("draft", "draft", [])).toEqual({ config: "waiting" });
    expect(tabStates("draft", "starting", [])).toEqual({ config: "running" });
    expect(tabStates("job", "running", pipeline({ decompile: "completed", extract_glossary: "running" }))).toEqual({ glossary: "running" });
    expect(tabStates("job", "paused", pipeline({ decompile: "completed", extract_glossary: "completed", resolve_glossary: "completed", approve_glossary: "paused" }))).toEqual({ glossary: "waiting" });
    expect(tabStates("job", "running", pipeline({ decompile: "completed", extract_glossary: "completed", resolve_glossary: "completed", approve_glossary: "completed", translate: "running" }))).toEqual({ progress: "running" });
    expect(tabStates("job", "paused", pipeline({ decompile: "completed", extract_glossary: "completed", resolve_glossary: "completed", approve_glossary: "completed", translate: "completed", compile: "paused" }))).toEqual({ review: "waiting" });
    expect(tabStates("job", "paused", pipeline({ decompile: "completed", extract_glossary: "paused" }))).toEqual({ progress: "waiting" });
    expect(tabStates("job", "complete", pipeline({}))).toEqual({});
  });
});

describe("stageActions", () => {
  it("offers resume and rerun on a stopped stage, rerun on a finished one", async () => {
    const { stageActions } = await import("./stages");
    const at = (name: string, status: string) => stageActions({ name, status, attempts: 1, message: "" });
    expect(at("audit_translation", "completed")).toEqual(["rerun"]);
    expect(at("translate", "failed")).toEqual(["resume", "rerun"]);
    expect(at("translate", "paused")).toEqual(["resume", "rerun"]);
    expect(at("approve_glossary", "paused")).toEqual([]); // glossary gate
    expect(at("compile", "paused")).toEqual([]); // final review gate
    expect(at("repair_translation", "pending")).toEqual([]);
    expect(at("repair_translation", "running")).toEqual([]);
  });
});

describe("directionLabel", () => {
  it("shows a translation direction as source → target", async () => {
    const { directionLabel, outputUrl } = await import("./format");
    expect(directionLabel("en-zh")).toBe("EN → ZH");
    expect(directionLabel("en-ja")).toBe("EN → JA");
    expect(directionLabel("")).toBe("—");
    expect(directionLabel(undefined)).toBe("—");
    expect(outputUrl("my book")).toBe("/api/jobs/my%20book/output");
  });
});

describe("seriesIdFromName", () => {
  it("turns a display name into a valid series id", async () => {
    const { seriesIdFromName } = await import("./series");
    expect(seriesIdFromName("The Qel Cycle")).toBe("the-qel-cycle");
    expect(seriesIdFromName("  Élan: Book  ")).toBe("elan-book");
    expect(seriesIdFromName("鲁滨逊")).toBe("");
  });
});

describe("workbench views", () => {
  it("files each term under the views it belongs to", async () => {
    const { matchesView, WORKBENCH_VIEWS } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", english: "Qelmar", chinese: "凯尔玛", category: "person", origin: "consensus",
      books: {}, decision: "keep", decided_by: "rule", reason: "", locked_from: null, ...patch,
    }) as Parameters<typeof matchesView>[0];
    const views = (t: Parameters<typeof matchesView>[0]) => WORKBENCH_VIEWS.map(([k]) => k).filter((k) => matchesView(t, k));
    expect(views(term({}))).toEqual(["keep", "all"]);
    expect(views(term({ suggestion: { kind: "promote", chinese: null, rationale: "r", model: "m" } }))).toEqual(["suggested", "keep", "all"]);
    expect(views(term({ origin: "conflict", decision: "pending" }))).toEqual(["pending", "conflict", "all"]);
    expect(views(term({ origin: "single_book", decision: "drop" }))).toEqual(["single_book", "drop", "all"]);
    expect(views(term({ origin: "carried", locked_from: "v001" }))).toEqual(["keep", "carried", "all"]);
    const hudson = term({ origin: "single_book", decision: "drop", books: { b3: ["哈德森"] }, mentions: { b1: 4, b3: 9 } });
    expect(views(hudson)).toEqual(["single_book", "elsewhere", "drop", "all"]);
    expect(matchesView(hudson, "book:b3")).toBe(true);
    expect(matchesView(hudson, "book:b1")).toBe(false);
  });
});

describe("suggestionEligible", () => {
  it("sends only undecided terms in scope for each task", async () => {
    const { suggestionEligible } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", english: "Qelmar", chinese: "凯尔玛", category: "person", origin: "consensus",
      books: {}, decision: "keep", decided_by: "rule", reason: "", locked_from: null, suggestion: null, ...patch,
    }) as Parameters<typeof suggestionEligible>[0];
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending" }), "conflicts")).toBe(true);
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending", decided_by: "user" }), "conflicts")).toBe(false);
    expect(suggestionEligible(term({}), "generic")).toBe(true);
    expect(suggestionEligible(term({ dismissed: ["drop_generic"] }), "generic")).toBe(false);
    expect(suggestionEligible(term({ origin: "carried", locked_from: "v001" }), "generic")).toBe(false);
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop" }), "promote")).toBe(true);
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop",
      suggestion: { kind: "promote", chinese: null, rationale: "r", model: "m" } }), "promote")).toBe(false);
  });
});
