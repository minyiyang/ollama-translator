import { describe, expect, it } from "vitest";
import {
  NO_OPT_INS,
  canPrefill,
  importsByChapter,
  matchesImportFilter,
  summarizeImport,
  willImport,
  type ImportItem,
} from "./xliffImport";

const item = (overrides: Partial<ImportItem>): ImportItem => ({
  unit_id: "u",
  segment_id: "s",
  document_id: "d1",
  category: "import",
  message: "",
  imported_text: "新",
  stale: false,
  edited_since_export: false,
  hard: [],
  overridable: [],
  ...overrides,
});

const finding = { category: "length", severity: "medium", message: "too long" };
const ALL = { include_stale: true, include_edited: true, include_overridable: true };

describe("willImport", () => {
  it("imports clean changes and nothing else by default", () => {
    expect(willImport(item({}), NO_OPT_INS)).toBe(true);
    for (const category of ["unknown_id", "unsupported_markup", "source_differs", "no_target", "unchanged"] as const) {
      expect(willImport(item({ category }), ALL)).toBe(false);
    }
  });

  it("needs the matching opt-in for stale, edited-since-export and overridable units", () => {
    const stale = item({ category: "stale", stale: true });
    const edited = item({ category: "edited_since_export", edited_since_export: true });
    const override = item({ category: "needs_override", overridable: [finding] });
    expect([stale, edited, override].map((i) => willImport(i, NO_OPT_INS))).toEqual([false, false, false]);
    expect(willImport(stale, { ...NO_OPT_INS, include_stale: true })).toBe(true);
    expect(willImport(edited, { ...NO_OPT_INS, include_edited: true })).toBe(true);
    expect(willImport(override, { ...NO_OPT_INS, include_overridable: true })).toBe(true);
  });

  it("needs every opt-in a unit qualifies for", () => {
    const both = item({ category: "stale", stale: true, overridable: [finding] });
    expect(willImport(both, { ...NO_OPT_INS, include_stale: true })).toBe(false);
    expect(willImport(both, { ...NO_OPT_INS, include_stale: true, include_overridable: true })).toBe(true);
  });

  it("never imports a unit with a hard finding", () => {
    expect(willImport(item({ category: "stale", stale: true, hard: [finding] }), ALL)).toBe(false);
    expect(willImport(item({ category: "fails_checks", hard: [finding] }), ALL)).toBe(false);
  });
});

describe("summarizeImport", () => {
  const items = [
    item({ segment_id: "a" }),
    item({ segment_id: "b", category: "stale", stale: true }),
    item({ segment_id: "c", category: "stale", stale: true, hard: [finding] }),
    item({ segment_id: "d", category: "unchanged" }),
    item({ segment_id: null, document_id: null, category: "unknown_id" }),
    item({ segment_id: "e", category: "needs_override", overridable: [finding] }),
  ];

  it("counts what imports, what's unchanged, and why the rest is skipped", () => {
    const summary = summarizeImport(items, NO_OPT_INS);
    expect(summary.willImport).toBe(1);
    expect(summary.unchanged).toBe(1);
    expect(summary.skipped).toBe(4);
    expect(summary.skippedBy).toEqual([["stale", 2], ["unknown_id", 1], ["needs_override", 1]]);
  });

  it("offers each opt-in only for units it would actually add", () => {
    expect(summarizeImport(items, NO_OPT_INS).optIns).toEqual({ stale: 1, edited: 0, overridable: 1 });
  });

  it("follows the opt-ins", () => {
    expect(summarizeImport(items, ALL).willImport).toBe(3);
  });
});

describe("importsByChapter", () => {
  it("counts imports per chapter", () => {
    const counts = importsByChapter(
      [item({ document_id: "d1" }), item({ document_id: "d1" }), item({ document_id: "d2", category: "unchanged" })],
      NO_OPT_INS,
    );
    expect([...counts]).toEqual([["d1", 2]]);
  });
});

describe("matchesImportFilter", () => {
  it("sorts book segments into the preview's filters", () => {
    const clean = item({});
    const skipped = item({ category: "fails_checks", hard: [finding] });
    const same = item({ category: "unchanged" });
    const cases: [ImportItem | undefined, string[]][] = [
      [clean, ["will_import", "all"]],
      [skipped, ["skipped", "all"]],
      [same, ["unchanged", "all"]],
      [undefined, ["not_in_file", "all"]],
    ];
    for (const [value, expected] of cases) {
      const matched = (["will_import", "skipped", "unchanged", "not_in_file", "all"] as const)
        .filter((filter) => matchesImportFilter(value, filter, NO_OPT_INS));
      expect(matched).toEqual(expected);
    }
  });
});

describe("canPrefill", () => {
  it("pre-fills the editor only where the file's text is worth fixing by hand", () => {
    expect(canPrefill("fails_checks")).toBe(true);
    expect(canPrefill("stale")).toBe(true);
    expect(canPrefill("source_differs")).toBe(false);
    expect(canPrefill("unknown_id")).toBe(false);
  });
});
