// XLIFF import review (docs/XLIFF_IMPORT.md). The server decides every unit's
// category; this mirrors its `will_import` rule so the "will import" count and
// filters follow the opt-in checkboxes live. The server re-applies the rule
// (and re-checks) when the import is applied.

export type ImportCategory =
  | "unknown_id"
  | "unsupported_markup"
  | "source_differs"
  | "no_target"
  | "unchanged"
  | "edited_since_export"
  | "stale"
  | "fails_checks"
  | "needs_override"
  | "import";

export type ImportFinding = { category: string; severity: string; message: string };

export type ImportItem = {
  unit_id: string;
  segment_id: string | null;
  document_id: string | null;
  category: ImportCategory;
  message: string;
  imported_text: string | null;
  imported_source?: string;
  current_text?: string;
  stale: boolean;
  edited_since_export: boolean;
  hard: ImportFinding[];
  overridable: ImportFinding[];
};

export type ImportPreview = {
  import_id: string;
  file_name: string;
  created_at: string;
  warnings: string[];
  counts: Partial<Record<ImportCategory, number>>;
  items: ImportItem[];
  error?: string;
};

export type SkippedItem = {
  unit_id: string;
  segment_id: string | null;
  document_id: string | null;
  category: ImportCategory;
  message: string;
  imported_text: string | null;
};

export type ImportResult = {
  import_id: string;
  file_name: string;
  applied_at: string;
  applied: { segment_id: string; document_id: string; event_id: string }[];
  skipped: SkippedItem[];
  unchanged_count: number;
  dropped_at_apply: number;
};

export type ImportState = { pending: ImportPreview | null; last: ImportResult | null };

export type ImportOptions = { include_stale: boolean; include_edited: boolean; include_overridable: boolean };

export const NO_OPT_INS: ImportOptions = { include_stale: false, include_edited: false, include_overridable: false };

export type ImportFilter = "will_import" | "skipped" | "unchanged" | "not_in_file" | "all";

export const CATEGORY_LABELS: Record<ImportCategory, string> = {
  unknown_id: "unknown ID",
  unsupported_markup: "unsupported markup",
  source_differs: "source differs",
  no_target: "no translation",
  unchanged: "unchanged",
  edited_since_export: "edited since export",
  stale: "stale",
  fails_checks: "fails checks",
  needs_override: "needs override",
  import: "import",
};

const ELIGIBLE = new Set<ImportCategory>(["import", "needs_override", "stale", "edited_since_export"]);

/** Whether a unit is imported under these opt-ins (the server's `will_import`). */
export function willImport(item: ImportItem, options: ImportOptions): boolean {
  if (!ELIGIBLE.has(item.category)) return false;
  if (item.stale && !options.include_stale) return false;
  if (item.edited_since_export && !options.include_edited) return false;
  if (item.hard.length) return false;
  return !item.overridable.length || options.include_overridable;
}

export type ImportSummary = {
  willImport: number;
  unchanged: number;
  skipped: number;
  skippedBy: [ImportCategory, number][];
  optIns: { stale: number; edited: number; overridable: number };
};

/** Counts for the preview card: what imports, what is skipped and why, and what each opt-in would add. */
export function summarizeImport(items: ImportItem[], options: ImportOptions): ImportSummary {
  let willCount = 0;
  let unchanged = 0;
  const skippedBy = new Map<ImportCategory, number>();
  for (const item of items) {
    if (willImport(item, options)) willCount += 1;
    else if (item.category === "unchanged") unchanged += 1;
    else skippedBy.set(item.category, (skippedBy.get(item.category) ?? 0) + 1);
  }
  const passes = (item: ImportItem) => ELIGIBLE.has(item.category) && !item.hard.length;
  return {
    willImport: willCount,
    unchanged,
    skipped: [...skippedBy.values()].reduce((sum, n) => sum + n, 0),
    skippedBy: [...skippedBy.entries()].sort((a, b) => b[1] - a[1]),
    optIns: {
      stale: items.filter((item) => item.stale && passes(item)).length,
      edited: items.filter((item) => item.edited_since_export && passes(item)).length,
      overridable: items.filter((item) => item.overridable.length > 0 && passes(item)).length,
    },
  };
}

/** How many segments each chapter would import, for the sidebar. */
export function importsByChapter(items: ImportItem[], options: ImportOptions): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    if (item.document_id && willImport(item, options)) counts.set(item.document_id, (counts.get(item.document_id) ?? 0) + 1);
  }
  return counts;
}

/** Whether a book segment (and its unit in the file, if any) shows under an import filter. */
export function matchesImportFilter(item: ImportItem | undefined, filter: ImportFilter, options: ImportOptions): boolean {
  switch (filter) {
    case "will_import": return !!item && willImport(item, options);
    case "skipped": return !!item && item.category !== "unchanged" && !willImport(item, options);
    case "unchanged": return item?.category === "unchanged";
    case "not_in_file": return !item;
    default: return true;
  }
}

/** Categories where the imported text is worth pre-filling into the editor to fix by hand. */
export function canPrefill(category: ImportCategory): boolean {
  return category === "stale" || category === "edited_since_export" || category === "fails_checks" || category === "needs_override";
}
