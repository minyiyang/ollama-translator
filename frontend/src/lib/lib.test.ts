import { describe, expect, it } from "vitest";
import { glossaryReviewMode, GLOSSARY_REVIEW_FLAGS } from "./configCatalog";
import { getPath, hasPath, sameValue, setPath, unsetPath } from "./configValues";
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

describe("configValues paths", () => {
  it("finds nothing for a setting the config does not have", () => {
    const values = { ollama: { model: "a" }, epub: null };
    expect(getPath(values, "ollama.model")).toBe("a");
    expect(getPath(values, "ollama.host")).toBeUndefined();
    expect(getPath(values, "ollama.model.name")).toBeUndefined(); // a string has no keys
    expect(getPath(values, "epub.strip")).toBeUndefined();
    expect(getPath(values, "audit.quantity.enabled")).toBeUndefined();
  });

  it("tells a setting turned off from a setting never mentioned", () => {
    const values = { ollama: { model: null, thinking: false }, top: 0 };
    expect(hasPath(values, "ollama.model")).toBe(true);
    expect(hasPath(values, "ollama.thinking")).toBe(true);
    expect(hasPath(values, "ollama.host")).toBe(false);
    expect(hasPath(values, "top")).toBe(true);
    expect(hasPath(values, "missing")).toBe(false);
    expect(hasPath(values, "top.inner")).toBe(false);
    expect(hasPath(values, "missing.inner.deep")).toBe(false);
  });

  it("can set a setting inside a section that was written as a plain value, and change one already set", () => {
    expect(setPath({ audit: "off" }, "audit.quantity.enabled", true)).toEqual({ audit: { quantity: { enabled: true } } });
    expect(setPath({ ollama: { model: "a", host: "h" } }, "ollama.model", "b")).toEqual({ ollama: { model: "b", host: "h" } });
    expect(setPath({}, "top", 1)).toEqual({ top: 1 });
  });

  it("leaves the config untouched when asked to remove a setting it does not have", () => {
    const values = { ollama: { model: "a" } };
    expect(unsetPath(values, "audit.model")).toBe(values);
    expect(unsetPath(values, "ollama.host")).toEqual(values);
    expect(unsetPath(values, "ollama")).toEqual({});
    expect(values).toEqual({ ollama: { model: "a" } });
  });

  it("counts two lists with the same items in the same order as the same value", () => {
    expect(sameValue(["a", "b"], ["a", "b"])).toBe(true);
    expect(sameValue(["a", "b"], ["b", "a"])).toBe(false);
    expect(sameValue({ a: 1 }, { a: 1 })).toBe(true);
    expect(sameValue(1, "1")).toBe(false);
    expect(sameValue(null, null)).toBe(true);
    expect(sameValue(null, false)).toBe(false);
  });
});

describe("config catalog", () => {
  it("indexes labels, help, and model settings by path", async () => {
    const { COMMON_GROUPS, LABELLED_PATHS, MODEL_PATHS, optionHelp, optionLabel } = await import("./configCatalog");
    expect(optionLabel("ollama.model")).toBe("Primary model");
    expect(optionHelp("ollama.temperature")).toBe("0 keeps output deterministic and resumable.");
    expect(optionHelp("ollama.host")).toBeUndefined(); // an option without help text
    expect(optionLabel("ollama.no_such_option")).toBeUndefined();
    expect(MODEL_PATHS.has("ollama.model")).toBe(true);
    expect(MODEL_PATHS.has("translation.fallback_models")).toBe(true);
    expect(MODEL_PATHS.has("audit.quantity.model")).toBe(true); // only under All settings
    expect(MODEL_PATHS.has("ollama.host")).toBe(false);

    const paths = COMMON_GROUPS.flatMap((g) => g.options).flatMap((o) => ("path" in o ? [o.path] : []));
    expect(new Set(paths).size, "an option is listed in two groups").toBe(paths.length);
    expect([...LABELLED_PATHS].sort()).toEqual([...paths].sort());
  });

  it("names the settings each special option writes", async () => {
    const { SPECIAL_PATHS } = await import("./configCatalog");
    expect(SPECIAL_PATHS["glossary-review"]).toEqual(["workflow.require_glossary_review", "workflow.llm_glossary_review"]);
    expect(SPECIAL_PATHS["language-pair"]).toContain("translation.direction");
  });

  it("turns a setting key into a label", async () => {
    const { humanize } = await import("./configCatalog");
    expect(humanize("max_num_ctx")).toBe("Max num ctx");
    expect(humanize("host")).toBe("Host");
    expect(humanize("")).toBe("");
  });

  it("reads LLM review as the mode even if the require flag is off", () => {
    expect(glossaryReviewMode(false, true)).toBe("llm");
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
  it("handles empty sides, pure insertions, and pure deletions", () => {
    expect(diffChars("", "")).toEqual([]);
    expect(diffChars("", "新")).toEqual([{ kind: "ins", text: "新" }]);
    expect(diffChars("旧", "")).toEqual([{ kind: "del", text: "旧" }]);
    expect(diffChars("你好世界", "你好，世界")).toEqual([
      { kind: "same", text: "你好" },
      { kind: "ins", text: "，" },
      { kind: "same", text: "世界" },
    ]);
    expect(diffChars("你好，世界", "你好世界")).toEqual([
      { kind: "same", text: "你好" },
      { kind: "del", text: "，" },
      { kind: "same", text: "世界" },
    ]);
  });
  it("treats an astral character as one unit", () => {
    expect(diffChars("a\u{1F600}b", "a\u{1F601}b")).toEqual([
      { kind: "same", text: "a" },
      { kind: "del", text: "\u{1F600}" },
      { kind: "ins", text: "\u{1F601}" },
      { kind: "same", text: "b" },
    ]);
  });
  it("round-trips: the parts rebuild both texts", () => {
    const before = "The quick brown fox jumps over the lazy dog.";
    const after = "A quick red fox leapt over the very lazy dog!";
    const parts = diffChars(before, after);
    expect(parts.filter((p) => p.kind !== "ins").map((p) => p.text).join("")).toBe(before);
    expect(parts.filter((p) => p.kind !== "del").map((p) => p.text).join("")).toBe(after);
  });
  it("falls back to one deletion and one insertion for very long changes", () => {
    const before = `<${"a".repeat(2100)}>`;
    const after = `<${"b".repeat(2100)}>`;
    expect(diffChars(before, after)).toEqual([
      { kind: "same", text: "<" },
      { kind: "del", text: "a".repeat(2100) },
      { kind: "ins", text: "b".repeat(2100) },
      { kind: "same", text: ">" },
    ]);
  });
});

describe("duration", () => {
  it("formats seconds, minutes and hours", () => {
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:00:45Z")).toBe("45s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T00:11:39Z")).toBe("11m 39s");
    expect(duration("2026-01-01T00:00:00Z", "2026-01-01T02:30:00Z")).toBe("2h 30m");
  });
  it("is empty without both ends and never negative", () => {
    expect(duration(undefined, "2026-01-01T00:00:45Z")).toBe("");
    expect(duration("2026-01-01T00:00:00Z", "")).toBe("");
    expect(duration("2026-01-01T00:00:45Z", "2026-01-01T00:00:00Z")).toBe("0s");
  });
});

describe("relativeTime", () => {
  it("names how long ago in the largest sensible unit", async () => {
    const { relativeTime } = await import("./format");
    const now = Date.parse("2026-09-28T12:00:00Z");
    const ago = (seconds: number) => relativeTime(new Date(now - seconds * 1000).toISOString(), now);
    expect(ago(0)).toBe("just now");
    expect(ago(89)).toBe("just now");
    expect(ago(90)).toBe("2 min ago");
    expect(ago(45 * 60)).toBe("45 min ago");
    expect(ago(89 * 60)).toBe("89 min ago");
    expect(ago(90 * 60)).toBe("2 h ago");
    expect(ago(35 * 3600)).toBe("35 h ago");
    expect(ago(36 * 3600)).toBe("2 d ago");
    expect(ago(10 * 86400)).toBe("10 d ago");
    expect(relativeTime("", now)).toBe("");
  });
});

describe("count", () => {
  it("groups thousands and shows nothing for zero or a missing number", async () => {
    const { count } = await import("./format");
    expect(count(1234567)).toBe((1234567).toLocaleString());
    expect(count(12)).toBe("12");
    expect(count(0)).toBe("");
    expect(count(undefined)).toBe("");
  });
});

describe("importReportUrl", () => {
  it("escapes the job and the import id", async () => {
    const { importReportUrl } = await import("./format");
    expect(importReportUrl("my book", "imp/1 a")).toBe("/api/jobs/my%20book/text/import/report?import_id=imp%2F1%20a");
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
    const { stageInfo } = await import("./stageInfo");
    for (const stage of Object.keys(STAGE_LABELS)) {
      const info = stageInfo(stage);
      expect(info, stage).toBeDefined();
      for (const part of ["does", "input", "output", "checks"] as const) expect(info![part].length, `${stage}.${part}`).toBeGreaterThan(10);
    }
    expect(stageInfo("no_such_stage")).toBeUndefined();
  });
});

describe("stageLabel", () => {
  it("falls back to the raw name for a stage it does not know", async () => {
    const { stageLabel } = await import("./stages");
    expect(stageLabel("compile")).toBe("Build the book");
    expect(stageLabel("future_stage")).toBe("future_stage");
  });
});

describe("attentionFrom", () => {
  it("flags the two gates that wait for a person", async () => {
    const { attentionFrom } = await import("./stages");
    const status = (stages: [string, string][]) => ({
      job_id: "demo", overall: "paused", configuration: { source_path: "" },
      stages: stages.map(([name, state]) => ({ name, status: state, attempts: 1, message: "" })),
    });
    expect(attentionFrom(status([["approve_glossary", "paused"], ["compile", "pending"]]))).toEqual({ glossary: true, review: false });
    expect(attentionFrom(status([["approve_glossary", "completed"], ["compile", "paused"]]))).toEqual({ glossary: false, review: true });
    expect(attentionFrom(status([["translate", "paused"]]))).toEqual({ glossary: false, review: false });
    expect(attentionFrom(undefined)).toEqual({ glossary: false, review: false });
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
  it("points a failed or stopped stage at Progress, and marks nothing before the run or after it", async () => {
    const { tabStates } = await import("./stages");
    expect(tabStates("job", "failed", pipeline({ decompile: "completed", extract_glossary: "failed" }))).toEqual({ progress: "waiting" });
    expect(tabStates("job", "failed", pipeline({ decompile: "completed", extract_glossary: "completed", resolve_glossary: "completed", approve_glossary: "failed" }))).toEqual({ progress: "waiting" });
    expect(tabStates("job", "running", pipeline({ decompile: "completed", extract_glossary: "completed", resolve_glossary: "completed", approve_glossary: "completed", translate: "completed", compile: "running" }))).toEqual({ progress: "running" });
    expect(tabStates("job", "pending", pipeline({}))).toEqual({}); // nothing has run yet
    expect(tabStates("job", "running", [])).toEqual({});
    expect(tabStates(undefined, "running", [st("translate", "running")])).toEqual({ progress: "running" });
    expect(tabStates("job", "running", [st("decompile", "completed"), st("translate", "skipped")])).toEqual({});
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

describe("lastStageAction", () => {
  it("names what last happened to a stage", async () => {
    const { lastStageAction } = await import("./stages");
    const at = (name: string, status: string, message = "", attempts = 1) =>
      lastStageAction({ name, status, attempts, message, updated_at: "2026-09-18T03:11:03+00:00" });
    expect(at("translate", "running")).toBe("started");
    expect(at("translate", "completed")).toBe("done");
    expect(at("translate", "failed")).toBe("failed");
    expect(at("translate", "paused", "stopped from the dashboard; resume to continue")).toBe("stopped");
    expect(at("translate", "paused", "paused on request; resume to continue")).toBe("paused");
    expect(at("compile", "paused", "result=pending; 2 segment(s) require human review")).toBe("waiting for review");
    expect(at("compile", "paused", "stopped from the dashboard; resume to continue")).toBe("stopped");
    expect(at("audit_translation", "pending")).toBe("reset");
    expect(at("audit_translation", "pending", "", 0)).toBeNull(); // never ran
    expect(lastStageAction({ name: "translate", status: "completed", attempts: 1, message: "" })).toBeNull();
  });
});

describe("shortTimestamp", () => {
  it("shortens the date the nearer it is", async () => {
    const { shortTimestamp } = await import("./format");
    const now = new Date(2026, 8, 28, 16, 30);
    const iso = (...parts: [number, number, number, number, number]) => new Date(...parts).toISOString();
    expect(shortTimestamp(iso(2026, 8, 28, 9, 5), now)).toBe("09:05");
    expect(shortTimestamp(iso(2026, 8, 18, 11, 8), now)).toBe("Sep 18 11:08");
    expect(shortTimestamp(iso(2025, 11, 31, 23, 59), now)).toBe("2025-12-31 23:59");
    expect(shortTimestamp("", now)).toBe("");
    expect(shortTimestamp("not a date", now)).toBe("");
  });
});

describe("directionLabel", () => {
  it("shows a translation direction as source → target", async () => {
    const { directionLabel, outputUrl, xliffExportUrl } = await import("./format");
    expect(directionLabel("en-zh")).toBe("EN → ZH");
    expect(directionLabel("en-ja")).toBe("EN → JA");
    expect(directionLabel("pt-BR>ja")).toBe("PT-BR → JA");
    expect(directionLabel("")).toBe("—");
    expect(directionLabel(undefined)).toBe("—");
    expect(outputUrl("my book")).toBe("/api/jobs/my%20book/output");
    expect(xliffExportUrl("my book")).toBe("/api/jobs/my%20book/text/export?format=xliff");
  });
});

describe("seriesIdFromName", () => {
  it("turns a display name into a valid series id", async () => {
    const { seriesIdFromName } = await import("./series");
    expect(seriesIdFromName("The Qel Cycle")).toBe("the-qel-cycle");
    expect(seriesIdFromName("  Élan: Book  ")).toBe("elan-book");
    expect(seriesIdFromName("鲁滨逊")).toBe("");
  });
  it("keeps dots and underscores inside, trims them at the ends, and caps the length", async () => {
    const { seriesIdFromName } = await import("./series");
    expect(seriesIdFromName("Vol_1.5 (draft)")).toBe("vol_1.5-draft");
    expect(seriesIdFromName("...Qel!!!")).toBe("qel");
    expect(seriesIdFromName("x".repeat(80))).toHaveLength(64);
  });
});

describe("workbench views", () => {
  it("files each term under the views it belongs to", async () => {
    const { matchesView, WORKBENCH_VIEWS } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", source: "Qelmar", target: "凯尔玛", category: "person", origin: "consensus",
      books: {}, decision: "keep", decided_by: "rule", reason: "", locked_from: null, ...patch,
    }) as Parameters<typeof matchesView>[0];
    const views = (t: Parameters<typeof matchesView>[0]) => WORKBENCH_VIEWS.map(([k]) => k).filter((k) => matchesView(t, k));
    expect(views(term({}))).toEqual(["keep", "all"]);
    expect(views(term({ suggestion: { kind: "promote", target: null, rationale: "r", model: "m" } }))).toEqual(["suggested", "keep", "all"]);
    expect(views(term({ origin: "conflict", decision: "pending" }))).toEqual(["pending", "conflict", "all"]);
    expect(views(term({ origin: "single_book", decision: "drop" }))).toEqual(["single_book", "drop", "all"]);
    expect(views(term({ origin: "carried", locked_from: "v001" }))).toEqual(["keep", "carried", "all"]);
    const hudson = term({ origin: "single_book", decision: "drop", books: { b3: ["哈德森"] }, mentions: { b1: 4, b3: 9 } });
    expect(views(hudson)).toEqual(["single_book", "elsewhere", "drop", "all"]);
    expect(matchesView(hudson, "book:b3")).toBe(true);
    expect(matchesView(hudson, "book:b1")).toBe(false);
  });
});

describe("single-book terms", () => {
  it("names the one book of a single-book term and the books whose text mentions it", async () => {
    const { mentioningBooks, singleBookOf } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", source: "Hudson", target: "哈德森", category: "person", origin: "single_book",
      books: { b3: ["哈德森"] }, decision: "drop", decided_by: "rule", reason: "", locked_from: null, ...patch,
    }) as Parameters<typeof singleBookOf>[0];
    expect(singleBookOf(term({}))).toBe("b3");
    expect(singleBookOf(term({ books: {} }))).toBe("");
    expect(singleBookOf(term({ origin: "consensus", books: { b1: ["x"], b3: ["x"] } }))).toBe(""); // shared, not single-book
    expect(mentioningBooks(term({ mentions: { b1: 4, b2: 0, b3: 9 } }))).toEqual(["b1", "b3"]);
    expect(mentioningBooks(term({}))).toEqual([]);
  });
});

describe("suggestionEligible", () => {
  it("sends only undecided terms in scope for each task", async () => {
    const { suggestionEligible } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", source: "Qelmar", target: "凯尔玛", category: "person", origin: "consensus",
      books: {}, decision: "keep", decided_by: "rule", reason: "", locked_from: null, suggestion: null, ...patch,
    }) as Parameters<typeof suggestionEligible>[0];
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending" }), "conflicts")).toBe(true);
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending", decided_by: "user" }), "conflicts")).toBe(false);
    expect(suggestionEligible(term({}), "generic")).toBe(true);
    expect(suggestionEligible(term({ dismissed: ["drop_generic"] }), "generic")).toBe(false);
    expect(suggestionEligible(term({ origin: "carried", locked_from: "v001" }), "generic")).toBe(false);
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop" }), "promote")).toBe(true);
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop",
      suggestion: { kind: "promote", target: null, rationale: "r", model: "m" } }), "promote")).toBe(false);
  });
  it("skips what a task does not cover or a person already dismissed", async () => {
    const { suggestionEligible } = await import("./series");
    const term = (patch: object) => ({
      term_id: "T1", source: "Qelmar", target: "凯尔玛", category: "person", origin: "consensus",
      books: {}, decision: "keep", decided_by: "rule", reason: "", locked_from: null, suggestion: null, ...patch,
    }) as Parameters<typeof suggestionEligible>[0];
    expect(suggestionEligible(term({ origin: "conflict", decision: "keep" }), "conflicts")).toBe(false); // already resolved
    expect(suggestionEligible(term({ origin: "consensus", decision: "pending" }), "conflicts")).toBe(false);
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending", dismissed: ["resolve"] }), "conflicts")).toBe(false);
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending", dismissed: ["promote"] }), "conflicts")).toBe(true);
    expect(suggestionEligible(term({ origin: "conflict", decision: "pending" }), "generic")).toBe(true);
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop" }), "generic")).toBe(false);
    expect(suggestionEligible(term({ origin: "single_book", decision: "keep" }), "promote")).toBe(false); // already promoted
    expect(suggestionEligible(term({ origin: "single_book", decision: "drop", decided_by: "llm-accepted" }), "promote")).toBe(false);
  });
});
