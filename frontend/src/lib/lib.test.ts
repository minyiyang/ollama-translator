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
