import { afterEach, describe, expect, it } from "vitest";
import { resetLocale, setLocale } from "../i18n";
import en from "../i18n/en.json";
import {
  approvalModeLabel, approvalResultLabel, editActionLabel, findingCategoryLabel, findingOriginLabel, glossaryBoundaryLabel,
  glossaryCategoryLabel, glossaryReviewLabel, reviewKindLabel, reviewModeLabel, roleLabel, severityLabel, statusLabel, styleLabel,
  tierLabel, versionLabel,
} from "./enums";

afterEach(resetLocale);

describe("enum labels", () => {
  it("shows a known value in English exactly as the server sends it", () => {
    const same: [(value: string) => string, string[]][] = [
      [statusLabel, ["pending", "running", "completed", "failed", "paused", "complete", "cancelled", "draft", "starting", "pausing"]],
      [tierLabel, ["tuned", "profiled", "generic"]],
      [severityLabel, ["low", "medium", "high", "info", "error"]],
      [findingCategoryLabel, [
        "structure", "empty", "untranslated", "omission", "addition", "duplication", "glossary", "mistranslation", "naturalness",
        "ai_style", "punctuation", "consistency", "readability", "repair", "verification", "quantity", "check",
      ]],
      [findingOriginLabel, [
        "initial_translation_audit", "initial_quantity_audit", "repair_history", "repair", "final_deterministic_audit",
        "final_semantic_verification", "final_quantity_audit", "book_consistency",
      ]],
      [reviewKindLabel, ["approval_required", "defect"]],
      [versionLabel, ["raw_translation", "semantic_repair", "prose_rewrite_candidate", "current_validated_draft"]],
      [editActionLabel, ["edit", "revert", "keep", "take_pipeline"]],
      [approvalResultLabel, ["approved", "rejected", "revised", "pending", "failed"]],
      [approvalModeLabel, ["deterministic", "llm"]],
      [reviewModeLabel, ["llm", "human", "none"]],
      [glossaryReviewLabel, ["LLM", "human", "none"]],
      [styleLabel, ["faithful", "natural", "literary", "concise", "classic", "young_adult", "fantasy", "science_fiction", "custom"]],
      [roleLabel, [
        "translation", "glossary resolution/approval", "translation, glossary resolution/approval", "glossary extraction",
        "semantic audit", "repair verification", "targeted repair", "quantity audit", "quantity escalation", "prose rewrite",
        "rewrite verification", "fallback translation #1", "fallback translation #12",
      ]],
      [glossaryBoundaryLabel, ["approved", "resolved", "not ready"]],
    ];
    for (const [labelOf, values] of same) {
      for (const value of values) expect(labelOf(value)).toBe(value);
    }
  });

  it("shows an unknown value as sent", () => {
    for (const labelOf of [
      statusLabel, tierLabel, severityLabel, findingCategoryLabel, findingOriginLabel, reviewKindLabel, versionLabel, editActionLabel,
      approvalResultLabel, approvalModeLabel, reviewModeLabel, glossaryReviewLabel, styleLabel, roleLabel, glossaryBoundaryLabel,
    ]) {
      expect(labelOf("brand_new")).toBe("brand_new");
      expect(labelOf("")).toBe("");
      // A name every object inherits is not a known value.
      expect(labelOf("constructor")).toBe("constructor");
      expect(labelOf("toString")).toBe("toString");
    }
  });

  it("takes the label from the catalog of the language in use", async () => {
    await setLocale("zh-CN", false);
    // Whatever the catalog says, or the English until it is translated: never the key.
    expect(statusLabel("paused")).not.toMatch(/^status\./);
    expect(statusLabel("brand_new")).toBe("brand_new");
  });

  it("names drafts that arrived joined, each by its own label", () => {
    expect(versionLabel("raw_translation, semantic_repair")).toBe("raw_translation, semantic_repair");
    expect(versionLabel("raw_translation, brand_new")).toBe("raw_translation, brand_new");
  });

  it("keeps a fallback model's number", () => {
    expect(roleLabel("fallback translation #3")).toBe(en["enum.role.fallbackTranslation"].replace("{number}", "3"));
    expect(roleLabel("fallback translation #")).toBe("fallback translation #");
  });
});

describe("glossary categories", () => {
  const server = { "人名": "Person", "地名": "Place", "组织": "Organization", "物品": "Item", "技术": "Technology", "概念": "Concept", "术语": "Term", "其他": "Other" };

  it("names every category as the server does in English", () => {
    for (const [value, name] of Object.entries(server)) {
      expect(glossaryCategoryLabel(value)).toBe(name);
      expect(glossaryCategoryLabel(value, server)).toBe(name);
    }
  });

  it("prefers the catalog to the server's label", () => {
    expect(glossaryCategoryLabel("人名", { "人名": "PERSON" })).toBe("Person");
  });

  it("falls back to the server's label, then to the value", () => {
    expect(glossaryCategoryLabel("作品", { "作品": "Work" })).toBe("Work");
    expect(glossaryCategoryLabel("作品", server)).toBe("作品");
    expect(glossaryCategoryLabel("作品")).toBe("作品");
    expect(glossaryCategoryLabel("作品", null)).toBe("作品");
    expect(glossaryCategoryLabel("constructor")).toBe("constructor");
  });
});
