# Translating between any two languages: plan

Status: **phases 1 to 3 implemented (section 6); decisions in section 5 made 2026-10-02.**

How the pipeline stops assuming English on one side and Simplified Chinese on
the other, so that a book can be translated between any two languages the
model handles, without weakening what `en-zh` and `zh-en` do today.

This is the "separate, larger project" that docs/LOCALIZATION.md sets aside.
The **interface language** stays independent of the **book languages**.

## 1. Where the code stands

The stage graph, checkpoints, edit log, review queue, compile, and XLIFF
round trip do not care about language. What does:

| Area | Today | Where |
|---|---|---|
| Language model | Two languages, two directions, as enums | `languages.py` |
| Glossary schema | Every entry is `english` + `chinese`; `english` must contain a Latin letter, `chinese` a CJK character or a preserved code. Used in about 14 modules and stored in every workspace and series file | `schemas.py`, `glossary.py`, `series.py`, `series_glossary.py`, `preprocessing.py`, `stages/glossary.py` |
| Glossary categories | Enum values are Chinese (`人名`, `地名`, …) and the legacy text format uses them as headings | `schemas.py` |
| Source/target selection | `if direction is EN_TO_ZH … else …` picks which field is the source | `translation.py`, `preprocessing.py`, `content_policy.py` |
| "Is this translated?" | Latin source with no CJK in the target (and the reverse); leftover Latin words; CJK runs | `audit.py`, `translation.py`, `content_policy.py` |
| Numbers and quantities | English and Chinese number words, clocks, fractions, magnitudes | `quantities.py`, `content_policy.py`, `numeric_adjudication.py` |
| Conventions | `——`, `“ ”`, `……`, straight quotes in Chinese text | `consistency.py`, `style_sheet.py`, `consistency_report.py` |
| Pronouns and address | `他/她/它`, `你/您`, `he/she/it` as fixed lists | `style_sheet.py`, `consistency.py`, `consistency_report.py` |
| Prompt examples | One- and few-shot marker examples written per direction | `translation.py`, `glossary_prompts.py`, `repair.py`, `prose_rewrite.py` |
| "AI-sounding" phrases | A phrase list per language | `audit.py` |
| Generic-term screen | A lowercase Latin word of 2–24 letters | `glossary.py` |
| Term matching | Case-folded, whole-word for English; exact substring for Chinese | `preprocessing.py`, `style_sheet.py` |
| Sentence splitting | `. ! ?` and `。！？` | `audit.py` |
| Token estimates | CJK characters count 1, everything else 4 bytes per token | `glossary.py`, `ollama_client.py` |
| Interface | "Direction" picker with two values; `EN → ZH` labels | `frontend/src/lib/configCatalog.ts`, `format.ts`, `pages/SeriesPage.tsx` |

Already neutral: prompts that name the languages from
`direction.*.display_name`; XLIFF `srcLang`/`trgLang`; `lang`, `xml:lang`, and
`dc:language` in EPUB compiled from RTF; the built-in style profiles (made
language-neutral in docs/PLAN.md). EPUB compiled from an EPUB keeps the
source's `dc:language` and `xml:lang` for every pair, `en-zh` included: a gap
found in phase 2 and not yet fixed.

## 2. Design

### 2.1 Languages are codes, not an enum

- Config gains `translation.source_language` and `translation.target_language`
  (BCP 47 codes: `en`, `zh-Hans`, `ja`, `de`, …).
- `translation.direction: en-zh` keeps working as shorthand for the pair it
  names, and `zh` keeps meaning Simplified Chinese.
- The direction's string form stays `"<source>-<target>"`. Stage input hashes
  already use that string, so **existing `en-zh` and `zh-en` jobs keep their
  checkpoints**. A test pins this.
- A series keeps one direction; a book joins only a series of its own pair
  (as today).

### 2.2 Language profiles

One `LanguageProfile` per language holds everything a check or prompt needs
to know about it. Code asks the profile; it never tests `== "zh"`.

| Field | Used for | `en` | `zh-Hans` |
|---|---|---|---|
| `display_name` | prompts, reports | English | Simplified Chinese |
| `script` | "is this translated?", term validation | Latin | Han |
| `spaced_words` | term matching, repeated-line keys, token estimates | yes | no |
| `cased` | glossary normalization, generic-term screen | yes | no |
| `sentence_end` | sentence splitting | `. ! ?` | `。！？` |
| `quotes`, `nested_quotes`, `dash`, `ellipsis` | conventions, style sheet | `“ ”`, `‘ ’`, `—`, `…` | `“ ”`, `‘ ’`, `——`, `……` |
| `pronouns`, `address_forms` | style sheet, pronoun report | he/she/it; none | 他/她/它; 你/您 |
| `number_words` | quantity extraction | parser | parser |
| `stock_phrases` | "AI-sounding" audit | list | list |
| `marker_examples` | one- and few-shot prompt examples | examples | examples |

A language **without a profile** gets a generic one built from its code and
Unicode data: the display name, the script, and whether words are spaced.
Every other field is empty.

### 2.3 Checks degrade, they do not misfire

Each check declares what it needs. When the profile lacks it, the check is
**skipped and reported as skipped**, never run with another language's rules.

| Check | Needs | Without it |
|---|---|---|
| Structure, markers, empty segments, duplication, mojibake | nothing | always runs |
| Glossary compliance, repeated lines, style-sheet expressions | `spaced_words` (generic profile has it) | always runs |
| Untranslated text | source and target scripts differ | same-script pairs (`en`→`de`): flag only a target identical to a long source; the semantic audit covers the rest |
| Leftover source words | `script`, `spaced_words` | skipped for same-script pairs |
| Digits, percentages, identifiers | nothing | always runs |
| Number words, clocks, fractions | `number_words` on both sides | sent to the quantity model (`numeric_adjudication`), which already rules on what the rules cannot |
| Punctuation conventions | `quotes`, `dash` | skipped |
| Pronouns and address | `pronouns`, `address_forms` | style-sheet characters keep `voice` only |
| "AI-sounding" phrases | `stock_phrases` | skipped |

The job summary lists the skipped checks, so a reviewer knows what the
pipeline did not look at.

### 2.4 Support tiers

| Tier | Meaning | Languages |
|---|---|---|
| Tuned | Full profile, benchmarked, prompts with examples | `en`, `zh-Hans` |
| Profiled | Profile filled in; checks run; one benchmark book | French and Japanese first (phase 5), then Spanish, Korean, Russian (phase 6) |
| Generic | Generic profile; universal checks only; more lands in human review | anything else, including pairs without English (`ja`→`zh-Hans`) |

The tier is shown when a job is created.

What the first languages ask of the profile, beyond filling in its fields:

| Language | What it exercises |
|---|---|
| French | Same script as English, so "is this translated?" cannot rely on script; `« »` with non-breaking spaces before `; : ! ?`; tu/vous as the address forms; elision (`l'`, `d'`) in whole-word term matching |
| Japanese | No spaces; three scripts in one text, sharing Han with Chinese, so a `ja`↔`zh-Hans` pair tells the sides apart by kana, not by Han; `「 」` and `『 』`; many pronouns and honorific suffixes (さん, 様) rather than one address form; kanji numerals |
| Spanish | `¿ ?` and `¡ !`; `« »` or `“ ”` by publisher, so the convention is per book; tú/usted |
| Korean | Spaced words but agglutinative particles, so a term matches as a prefix of a word; speech levels instead of an address pronoun; Sino-Korean and native number words |
| Russian | Cyrillic; case inflection, so glossary compliance needs stem matching or the semantic audit; `« »` and `—` for dialogue; ты/вы |

Korean and Russian show that exact term matching does not hold for inflected
targets. Glossary compliance for them starts as "stem present" and leaves
the form to the semantic audit; this is settled when their profiles are
written, not before.

### 2.4.1 Traditional Chinese

Not a profile yet. `zh` keeps meaning Simplified. Before deciding between a
`zh-Hant` language and a variant of `zh-Hans`, a **capability probe** on the
models in use: translate a fixed sample into Traditional Chinese and
measure, per model,

- how many segments contain Simplified-only characters (deterministic, from
  a conversion table);
- whether regional vocabulary is stable (Taiwan and Hong Kong usage differ)
  or mixed;
- whether glossary renderings supplied in Traditional are kept.

If a model writes clean Traditional, `zh-Hant` becomes a profiled language
that shares the `zh-Hans` number words and conventions (with `「 」` quotes).
If not, the alternative to weigh is translating into Simplified and
converting deterministically at compile. The probe is a script under
`scripts/`, run once per model; its result is recorded here.

### 2.5 Glossary

- Entries become `source` + `target` (+ `aliases`, which are source-side).
  The old `english`/`chinese` names are still **read**, mapped by the
  glossary's direction, so existing workspaces, series, and seed files load
  unchanged. New files are written with the new names.
- Validation comes from the profiles: a term must contain a character of its
  language's script, or be a preserved code.
- A glossary records its language pair, and an entry belongs to that pair
  (decision 3): there is no multi-language entry. Using an `en-zh` glossary
  for a `zh-en` job (allowed today) becomes an explicit swap on load.
- Categories keep their stored values (`人名`, …); the interface shows a
  label per interface language (docs/LOCALIZATION.md, 2.5). The legacy text
  format keeps its Chinese headings and stays `en`/`zh` only.
- The generic-term screen applies only to a `cased` source language. For the
  others, low-confidence entries go to review instead.

### 2.6 Prompts

- Instructions stay in English and name both languages, as now.
- Marker and repair examples come from the target profile. Without them the
  prompt uses the existing `none` example mode plus the marker rules in
  words.
- Extraction instructions for the style sheet list the target profile's
  pronouns and address forms, or omit that sentence.
- Prompts for `en-zh` and `zh-en` must stay **byte-for-byte the same**, since
  prompt text feeds unit hashes. A test compares them with a recorded copy.

### 2.7 Interface

- "Direction" becomes two language selectors, each listing profiled languages
  first and accepting any code.
- The job list shows `EN → JA` from the codes; a series shows its pair.
- The Config tab hides options whose checks are skipped for the pair, with a
  line saying why.

### 2.8 Model

Quality depends on the local model. Many models are much weaker outside
English and Chinese, and weaker still between two non-English languages.
The plan does not add pivot translation through English; it reports the
risk at job creation and leaves the choice of model to the user.

## 3. Quality checks

| Check | How |
|---|---|
| No change for today's pairs | Recorded prompts and stage hashes for `en-zh` and `zh-en` fixtures; any difference fails |
| Old files still load | Fixtures of today's glossary, series, and workspace files read through the new schema |
| No hard-coded language tests | A test fails on `== "zh"`, `Language.CHINESE`, `EN_TO_ZH`, or a private CJK regex outside the profiles |
| Generic tier works | The fixture pipeline end to end for a different-script pair (`en`→`ja`) and a same-script pair (`en`→`de`), with fake clients |
| Skips are honest | Each check's "needs" is tested against a profile that lacks it: the check reports skipped and raises nothing |
| A profile is complete | Per profiled language: number words round-trip, conventions and examples present |
| Real quality | One short public-domain book per profiled language, measured with `consistency_report` and a native reader's sample |

## 4. Phases

| Phase | Work | Visible result |
|---|---|---|
| 1 | `LanguageProfile` for `en` and `zh-Hans`; every language test in the code reads the profile; codes replace the enums; hash and prompt recordings | None: `en-zh` and `zh-en` behave exactly as before. A refactor covered by existing tests. |
| 2 | Generic profile; checks declare their needs and report skips; untranslated-text check for same-script pairs; config and CLI accept any pair | A third language translates end to end at the generic tier |
| 3 | Glossary `source`/`target` with old names read; profile-driven validation; series pair; swap on load | Glossary, series, and style sheet work for any pair |
| 4 | Interface: language selectors, tier notice, skipped-check notes | Jobs for any pair can be created from the dashboard |
| 5 | French and Japanese profiles: conventions, address forms, number words, prompt examples; one benchmark book each | French and Japanese at the profiled tier, to and from English and Chinese |
| 6 | Spanish, Korean, Russian profiles; stem matching for inflected targets | Three more profiled languages |
| alongside | Traditional Chinese capability probe (2.4.1) | A recorded result and a decision |
| later | Each further language: one profile plus a benchmark run | New entry in the profiled list |

Phase 1 is the largest and carries the regression risk; it changes no
behavior, so it can merge on its own. Phase 3 touches stored data and does
not start before phase 1 is settled. All of phases 1 to 6 are in scope.

## 5. Decisions

Made 2026-10-02:

1. **First languages:** French and Japanese, then Spanish, Korean, Russian.
2. **Traditional Chinese:** undecided until the model capability probe
   (2.4.1) has run.
3. **Glossary entries:** `source`/`target` per language pair.
4. **Pairs without English:** supported at the generic tier from phase 2.
5. **Glossary rename (phase 3):** done with the rest, not deferred.

Open:

- Traditional Chinese, after the probe: its own profiled language, or
  Simplified converted at compile.
- Term matching for inflected targets (Korean, Russian), when their
  profiles are written.

## 6. Implementation notes

Agreed 2026-10-02, after reading the plan against the code.

1. **Model-facing field names follow the pair.** The resolution and approval
   schemas and their JSON examples name the field `chinese`, and prompt text
   feeds unit hashes. Phase 3 renames the stored glossary fields to
   `source`/`target`, but what the model sees keeps the pair's own words:
   `english`/`chinese` for `en`↔`zh` (byte-for-byte as today), `source`/
   `target` for any other pair. A thin adapter maps between them.
2. **The pair string is only parsed for today's shorthand.** `en-zh` and
   `zh-en` stay exactly as they are (every stage hash and stored artifact
   uses them). Any other pair is written `<source>><target>` (`en>ja`,
   `zh-Hant>en`), so codes with subtags stay unambiguous. Config holds
   `source_language` and `target_language` explicitly; the string is never
   split except to read the two legacy values.
3. **Stored models keep their direction string.** `direction` is a field of
   33 stored models (`TranslatedDocument`, `DocumentAudit`, …), serialized as
   `"en-zh"`. The pair type that replaces `TranslationDirection` reads and
   writes that same string in phase 1, so existing artifacts load unchanged.
4. **Config serialization stays the same for today's pairs.** Several stage
   hashes include a config section's JSON (`translation.model_dump_json()`
   feeds translate and repair). New language fields are left out of that
   JSON when they only restate `en-zh`/`zh-en`, so adding them does not
   invalidate existing checkpoints.

**Phase 1 steps:**

1. Safety net, before any change: golden prompts and stage input hashes for
   `en-zh` and `zh-en` fixture jobs; a scanner that fails on hard-coded
   language tests, starting with an allow-list of today's sites.
2. `LanguageProfile` for `en` and `zh-Hans` (`zh` as an alias) and the pair
   type in `languages.py`; `TranslationDirection` stays as an alias so its
   33 imports change later, not in the same step.
3. Each rule moves into the profile one module at a time (script detection,
   sentence ends, conventions, pronouns and address forms, stock phrases,
   marker examples, number words, token estimates), each change checked
   against the golden files.
4. The allow-list is emptied; the scanner becomes strict.

**Phase 1 status (2026-10-02): done, pending review.**

- `book_agent/languages.py` holds the `en` and `zh-Hans` profiles (stored
  code `zh`). `Language` and `LanguagePair` are string codes that serialize
  exactly as the old enums did; `TranslationDirection` is an alias of
  `LanguagePair`, and `EN_TO_ZH`/`ZH_TO_EN` remain for existing imports. A
  pair outside the profiles (`en>ja`) is parsed but rejected until phase 2
  adds the generic profile.
- Every module asks the profile now. `tests/test_language_scanner.py` has no
  allow-list, and it also catches Han ranges written as literal characters
  and `startswith("en")`. `tests/test_language_golden.py` shows prompts,
  schemas, and stage hashes unchanged for both pairs. The full backend suite
  passes.
- One deliberate difference: a quotation kept from the source is no longer
  read as foreign Latin text when it contains rare Han characters (CJK
  Extension A, compatibility ideographs). Before, only the basic Han block
  was excluded.

Left for later phases, and named in code by `profile("zh")` rather than
hidden:

- Chinese number words, clocks, and fractions (`content_policy.py`,
  `quantities.py`) still run on every text, as before. They become the `zh`
  profile's `number_words` parser in phase 5, when French and Japanese add
  their own.
- The pronoun, address, and punctuation reports in `consistency_report.py`
  and `consistency.py` run only for a profile with `convention_checks`
  (today `zh`). Their rules are still written for Chinese.
- The stored `english`/`chinese` glossary fields and their validators wait
  for phase 3.
- The prose-rewrite stage (`prose_rewrite.py`) is written for Chinese
  targets: its prompt and candidate rules are Chinese. Only `reprose.enabled`
  switches it, not the direction, so phase 2 should give it a "needs" and
  skip it for other targets.

**Phase 2 status (2026-10-02): done, pending review.**

- **Generic profile.** Any well-formed BCP 47 code is a language. A code
  without a written profile gets one from tables in `languages.py`: an
  English name, its scripts (`ja` is Han + Hiragana + Katakana; a script
  subtag such as `sr-Latn` or `zh-Hant` overrides), and whether it spaces
  words. A regional variant of a tuned language (`en-GB`) uses that
  language's profile; `zh-TW`/`zh-HK` are Traditional and generic. An
  unknown script matches any letter, so the passage still counts as prose.
- **Pairs.** `en>ja`, or `translation.source_language`/`target_language`.
  These two fields appear in the saved config only for other pairs, so
  `en-zh`/`zh-en` configs and hashes are unchanged (golden test).
  `LanguagePair.slug` (`en-ja`) names files and jobs, since `>` is not
  allowed in Windows file names.
- **Checks degrade.** Scripts decide what can be checked:
  - "No target text" runs only when the two scripts are disjoint.
  - Left-over source text looks only for scripts the target never uses:
    kana in a `ja>zh` translation counts, kanji in `zh>ja` does not.
  - In a pair sharing a script, "identical to source" needs 20 or more
    letters ("Paris." is fine).
  - With no marker examples, the prompt keeps the marker rules in words.
  - Without pronouns, the style-sheet extraction leaves that sentence out.
  - English or Chinese number words with no counterpart go to the existing
    numeric ruling, which marks the segment uncertain when no ruling comes
    back.
- **Not available yet.**
  - Glossary and style sheet (phase 3). The extraction stage takes its
    "disabled" path, the approval gate has nothing to review, and config
    refuses glossary files.
  - A series for another pair.
  - Prose rewrite for a non-tuned target. Config refuses `reprose.enabled`.
- **Reported.** `language_support()` gives the tiers and the skipped
  checks with reasons. It appears in `run --dry-run` and in
  `workflow_status` (`status --json`) and the web job header's `job_info` under `languages`.
- **Tested.** `tests/test_language_generic.py` covers codes, profiles,
  pairs, config round trip and refusals, skip lists, and the script-aware
  untranslated checks. It also runs `en>ja` and `en>de` end to end through
  the real workflow with fake model clients, both gates included. The full
  backend suite passes.

**Phase 3 status (2026-10-02): done, pending review.** It supersedes phase
2's "not available yet" list: glossary, style sheet, and series now work
for any pair.

Decisions taken while implementing it:

5. **New files use `source`/`target` for every pair** (chosen 2026-10-02 over
   keeping `english`/`chinese` on disk for en/zh). Every glossary, review
   file, workbench, report, and preprocessed document is written with the
   new names, and a glossary records its `pair`. Older files still load:
   - entries without a pair are read as `en-zh`;
   - `canonical_english`, `selected_chinese`, and the workbench's and
     approval records' `english`/`chinese` are renamed on read.
   New en/zh jobs therefore get new glossary and stage hashes (the golden
   stage hashes were re-recorded on purpose). Existing jobs keep their
   checkpoints unless their glossary is approved again.
6. **An en/zh book's glossary is `en-zh` either way.** The glossary pipeline
   has always keyed entries by the English term and resolved a Chinese
   rendering, whatever the book's direction. So `glossary_pair(zh-en)` is
   `en-zh`, and a zh-en job uses its glossary the other way round through
   `glossary_sides`. This is the doc's "swap on load" for that case, with
   no data rewritten. Every other job's glossary has the job's own pair and
   is keyed by its source term.

What changed:

- **Model.** `GlossaryEntry` has `source`/`target`; `GlossaryResult` has
  `pair`. Terms are validated against the pair's scripts through a
  validation context that containers (a glossary, a preprocessed document)
  set for their entries. A target may repeat a preserved code exactly.
- **What the model sees.** `english`/`chinese` for an en-zh glossary,
  byte-for-byte as before: the golden prompts and schemas are unchanged,
  including the series-conflict, series-suggestion, and style-sheet
  schemas. Any other pair sees `source`/`target`, with prompts that name
  both languages and never mention Chinese.
- **Loading.** `orient_glossary` accepts a file in the job's glossary pair
  as is, swaps the exact reverse explicitly (dropping aliases, which spelled
  the old source term), and refuses an unrelated pair. It applies to seed,
  series, bound-series, and reviewed glossaries and to series imports.
- **Series.** A series glossary has the series' glossary pair; a series can
  be created for any pair. The workbench view reports the pair for labels;
  CLI `series decide` takes `--target` (`--chinese` still works).
- **Style sheet.** Pronoun and address are stored as text. The model and
  the reviewer choose from the target profile's lists (the tuned pairs keep
  their combined en/zh lists), and reviewed sheets are checked against
  them. A target without pronouns is reported as skipping "style-sheet
  pronouns".
- **Harmonization.** Name-part alignment uses the target profile's
  `name_separator` (`·` for Chinese) and is skipped without one.
- **Interface.** Glossary and series pages use `source`/`target` and label
  columns with the pair's language names (`frontend/src/lib/languages.ts`).
- **Tested.** `tests/test_glossary_pairs.py` (old files, validation,
  orientation, model-facing names and prompts, series, style choices).
  `tests/test_language_generic.py` runs `en>ja` and `en>de` end to end with
  a seed glossary in their own pair, and checks that the approved rendering
  reaches the translation prompt. The full backend suite (1,102) and the
  frontend tests (83) pass.

Left for phase 4: language selectors in the job form and the Config tab
(the Direction picker still lists en-zh and zh-en; other pairs are set in
YAML), the tier notice, and skipped-check notes in the interface.
