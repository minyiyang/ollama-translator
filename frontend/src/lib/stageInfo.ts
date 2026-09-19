// Plain-language description of each pipeline stage, shown as a tooltip on the
// Progress tab. Keep in sync with docs/DESIGN.md (§4 workflow, §9 validation).

export type StageInfo = {
  does: string;
  input: string;
  output: string;
  checks: string;
  model?: string; // config key naming the model, when the stage calls one
  pauses?: string;
};

export const STAGE_INFO: Record<string, StageInfo> = {
  decompile: {
    does: "Unpacks the EPUB (or parses the RTF) into chapters and text segments with stable IDs. Inline markup such as emphasis and links becomes protected markers so it survives translation.",
    input: "The source book.",
    output: "Decompile manifest: chapters, segments, and the original package resources.",
    checks: "Safe archive paths and well-formed XML. No model is used.",
  },
  extract_glossary: {
    does: "Reads the book in chunks and proposes terms that must be translated consistently: names, places, items, concepts.",
    input: "All text segments.",
    output: "Merged glossary candidates, each with evidence sentences from the book.",
    checks: "Every candidate must cite real segments; generic words are screened out.",
    model: "glossary.extraction_model",
  },
  resolve_glossary: {
    does: "Merges candidates into one Chinese rendering and category per term, resolving conflicts in small batches and then a conflict-only pass. Seed, series, and book glossaries take precedence.",
    input: "Glossary candidates and any configured glossary files.",
    output: "Draft glossary plus a quality report (generic terms, shared renderings, missing proper nouns).",
    checks: "The resolver cannot invent terms; every rendering must contain Chinese. An invalid rendering falls back to the extracted candidate.",
    model: "ollama.model",
  },
  approve_glossary: {
    does: "The glossary gate. Either you review it on the Glossary tab, or the LLM reviewer approves, rejects, or revises the uncertain entries (evidence-backed, high-confidence terms pass automatically).",
    input: "Draft glossary.",
    output: "Approved glossary and an approval report recording every decision.",
    checks: "Reviewed files must match the glossary schema; the reviewer cannot add English terms.",
    model: "ollama.model (LLM review only)",
    pauses: "Pauses here when the config asks for human glossary review.",
  },
  preprocess: {
    does: "Picks, for each document and chunk, the glossary entries that actually occur there (longest unambiguous matches) and attaches them with their notes. The source text itself is not rewritten.",
    input: "Decompiled segments and the approved glossary.",
    output: "Preprocessed documents: protected source plus per-chunk glossary.",
    checks: "Case-sensitive terms must match case; conflicting nested terms are left to context. No model.",
  },
  translate: {
    does: "First-draft translation of every chunk, using the chunk's glossary entries and the configured prose style.",
    input: "Preprocessed chunks.",
    output: "Translated documents, one per chapter.",
    checks: "Exact marker sequence, paragraph IDs and order, Chinese present, sane length ratio. Failures get a focused retry; passages that still fail are deferred to repair instead of stopping the run.",
    model: "ollama.model",
  },
  rescue_translation: {
    does: "Redrafts passages the primary model gave up on, using the fallback model, then lets the primary model harmonize the wording so the book keeps one voice.",
    input: "Deferred passages from Translate.",
    output: "Rescued passages merged back into the translated documents.",
    checks: "The same contract as Translate. Skipped when no fallback models are configured.",
    model: "translation.fallback_models",
  },
  audit_translation: {
    does: "Checks every segment deterministically (numbers, untranslated text, length, markers), then has a second model review risky passages for meaning errors.",
    input: "Translated documents and the source.",
    output: "Findings per document, each with segment, severity, category, explanation, and a concrete fix.",
    checks: "Findings without a concrete fix, or that only state a style preference, are dropped.",
    model: "audit.model",
  },
  repair_translation: {
    does: "Rewrites only the segments with findings at or above the repair threshold; nothing else is touched.",
    input: "Audit findings.",
    output: "Repaired draft and a record of every repair.",
    checks: "A semantic repair must win a neutral A/B comparison against the original; a repair that cannot be verified is discarded and the segment goes to human review.",
    model: "audit.repair_model (default: ollama.model); verified by audit.verifier_model",
  },
  reprose_translation: {
    does: "Optional polish: rewrites passages that read like translationese into more natural prose.",
    input: "Repaired draft.",
    output: "Accepted rewrites; rejected ones are kept on record.",
    checks: "Markers, glossary terms, quantities, negation, modality, and quotation structure must survive, and a verifier must judge the rewrite materially better; otherwise the original stays.",
    model: "reprose.model; verified by reprose.verifier_model",
  },
  review_repaired: {
    does: "Re-verifies the repaired draft: deterministic preflight, then a full verification pass that records rejected segments. It changes no text.",
    input: "Repaired (and reprosed) draft.",
    output: "List of segments that need another repair.",
    checks: "Verifier findings must name a concrete problem.",
    model: "audit.verifier_model (default: audit.model)",
  },
  repair_review: {
    does: "Repairs the segments Review rejected, using the verifier's feedback.",
    input: "Rejected segments and their findings.",
    output: "Review-repaired candidates.",
    checks: "If a correction is still rejected, the last accepted text is restored and the segment goes to human review.",
    model: "ollama.model",
  },
  validate_repaired: {
    does: "Final gate before compiling: deterministic audit of every segment plus one independent check of the segments changed during review.",
    input: "Review-repaired draft.",
    output: "Validated documents and the human-review queue (worksheet in reports/).",
    checks: "Blocking findings keep a segment in the review queue; nothing is rewritten here.",
    model: "audit.verifier_model (default: audit.model)",
  },
  compile: {
    does: "Rebuilds the EPUB: translated text goes back into the original markup, markers become the original inline elements, all resources are kept.",
    input: "Validated documents and the original package.",
    output: "The translated EPUB in output/.",
    checks: "Local references, spine, XML, and CSS are validated. No model.",
    pauses: "Pauses for the Final review tab when more flagged segments remain than the config allows, or when final approval is required.",
  },
  validate_epub: {
    does: "Reopens the compiled EPUB and proves it: package structure, spine, references, and that its text matches the accepted translations.",
    input: "Compiled EPUB and the validated documents.",
    output: "Validation report; the job is complete when this passes.",
    checks: "Any mismatch fails the job instead of shipping a broken book. No model.",
  },
};
