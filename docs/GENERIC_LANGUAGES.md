# Translating between any two languages: plan

Status: **phases 1 to 6 implemented and benchmarked (section 6); decisions in section 5 made 2026-10-02; what the models and the pipeline can do, and their limits, in section 7 (2026-10-04).**

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
| Leftover source words | `script`, `spaced_words`; for a same-script pair, `function_words` on both sides | two spaced languages: a sentence, or six words in a row, in the words of the source passage itself (`source_worded_passage`). Skipped only where one side is unspaced and neither rule applies |
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
| Profiled | Profile filled in; checks run; one benchmark book | French and Japanese first (phase 5), then Spanish, Korean, German (phase 6) |
| Generic | Generic profile; universal checks only; more lands in human review | anything else, including pairs without English (`ja`→`zh-Hans`) |

The tier is shown when a job is created.

What the first languages ask of the profile, beyond filling in its fields:

| Language | What it exercises |
|---|---|
| French | Same script as English, so "is this translated?" cannot rely on script; `« »` with non-breaking spaces before `; : ! ?`; tu/vous as the address forms; elision (`l'`, `d'`) in whole-word term matching |
| Japanese | No spaces; three scripts in one text, sharing Han with Chinese, so a `ja`↔`zh-Hans` pair tells the sides apart by kana, not by Han; `「 」` and `『 』`; many pronouns and honorific suffixes (さん, 様) rather than one address form; kanji numerals |
| Spanish | `¿ ?` and `¡ !`; `« »` or `“ ”` by publisher, so the convention is per book; tú/usted |
| Korean | Spaced words but agglutinative particles, so a term matches as a prefix of a word; speech levels instead of an address pronoun; Sino-Korean and native number words |
| German | Same script as English and French; nouns capitalized and compounded, number words written as one word (dreihundertfünfundzwanzig); case endings (des Käfers), so glossary compliance needs stem matching; `„ “` or `» «` by publisher; du/Sie |

Korean and German show that exact term matching does not hold for inflected
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
| 6 | Spanish, Korean, German profiles; stem matching for inflected targets | Three more profiled languages |
| alongside | Traditional Chinese capability probe (2.4.1) | A recorded result and a decision |
| later | Each further language: one profile plus a benchmark run | New entry in the profiled list |

Phase 1 is the largest and carries the regression risk; it changes no
behavior, so it can merge on its own. Phase 3 touches stored data and does
not start before phase 1 is settled. All of phases 1 to 6 are in scope.

## 5. Decisions

Made 2026-10-02:

1. **First languages:** French and Japanese, then Spanish, Korean, German
   (German replaced Russian on 2026-10-02, for the sample books at hand).
2. **Traditional Chinese:** undecided until the model capability probe
   (2.4.1) has run.
3. **Glossary entries:** `source`/`target` per language pair.
4. **Pairs without English:** supported at the generic tier from phase 2.
5. **Glossary rename (phase 3):** done with the rest, not deferred.

Open:

- Traditional Chinese, after the probe: its own profiled language, or
  Simplified converted at compile.
- Term matching for inflected targets (Korean, German), when their
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
  The stage hashes are the same on every platform and are compared on each.
  They were not at first: the decompile manifest listed a package's files in
  the platform's path order (Windows ignores case) and took the media type of
  a file the package does not declare from the system's table (the registry,
  `/etc/mime.types`), and every later stage hashes what came before it. The
  manifest now orders files without regard to case (`archive_order`) and
  asks Python's own table (`guess_media_type`); the hashes recorded on
  Windows did not change, and Linux now gives the same ones.
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

**Phase 4 status (2026-10-02): done, pending review.**

- **Languages picker** (`LanguagePairPicker`). It replaces the Direction
  setting on the Config tab and the direction select when creating a series.
  - Two code inputs list the tuned languages first, then common languages by
    name (`language_catalog()`, served at `GET /api/languages`).
  - Any other BCP 47 code is accepted once typed in full.
  - A swap button reverses the pair.
  - It writes `translation.direction` in the stored spelling (`en-zh`,
    `zh-en`, or `src>tgt`) and clears `source_language`/`target_language`.
- **Tier and model notice.** For a pair beyond the tuned languages, the
  picker and the setup check show each side's tier, the model-quality notice
  of 2.8 (`language_support()["notice"]`), and the skipped checks with their
  reasons. The setup summary carries them as `summary.languages`.
- **Hidden options.** On the Config tab, the options of a skipped check are
  hidden, with a line naming them and the reason:
  - prose rewrite's switch, mode, and models;
  - punctuation conventions.
  The All settings and YAML views still show everything.
- **Labels.** The job list and series show any pair as `EN → JA` or
  `PT-BR → JA`. A generic-tier job's header shows its pair and how many
  checks are skipped, with the list as a tooltip.
- **Build.** The dashboard bundle in `book_agent/web/static` is rebuilt.
- **Tested.** `LanguagePairPicker.test.tsx` and `ConfigEditor.test.tsx` in
  the frontend (91 tests). On the backend, the catalog, the notice, and
  `/api/languages` are covered over HTTP (1,104 tests).

**Phase 5 status (2026-10-02): profiles done and benchmarked (results below).**

- **Profiles.** `fr` and `ja` are at the profiled tier (`PROFILES` in
  `languages.py`).
  - French: « » with no-break spaces, nested “ ”, il/elle, tu/vous, stock
    phrases, marker examples, an `s`/`x` plural in term matching. Elided
    articles (`d'Aster`) still match the term.
  - Japanese: 「」 and 『』, ……, 彼/彼女, the honorifics さん/様/君/ちゃん as
    forms of address, `・` between the parts of a katakana name. Its scripts
    are Han + kana, so a `ja`/`zh` pair tells the sides apart by kana.
- **The tuned set is frozen.** `TUNED_PROFILES` (en, zh) builds every pattern
  that spans "all languages": any script, word runs, lexical tokens,
  sentence ends, the token estimate, and the pair list. A new profile
  therefore never changes en-zh or zh-en (golden test unchanged).
- **Conventions are data.** A profile lists `ConventionRule`s (the house
  form, the slip, the message), and the book-level check enforces them by
  majority. Chinese keeps its two rules with the same messages. French adds
  quote style and the no-break space before `; : ! ?`; Japanese adds quote
  style and the ellipsis. The check keeps no-break spaces when it normalizes
  text. A slip with a mechanical correction is corrected without a model: a
  German closing quote (`replace`), and a plain space, or none, before a
  French `; : ! ?` (`substitute`; a time like 10:30, an address, and a second
  mark are left alone). A repair keeps a convention its line kept, or the
  book keeps by the same majority the check counts (`keep_conventions`,
  `book_conventions`): the repair model wrote plain spaces where the French
  line had no-break ones, and one run's typography went from 84 to 60 such
  spaces against 31 to 50 plain ones, 33 lines then queued for a person.
  Kept for the line only, 7 were still queued: repairs that added a question
  to a line that had none.
  The consistency report's character and convention sections stay
  Chinese (`consistency_report`), reported as the separate skips
  "punctuation conventions" and "character report".
- **Number words.** `number_words` names a parser. English and Chinese still
  read every text. French (`number_words.py`: cardinals, the "et" rule,
  quatre-vingts, percentages) and Japanese (億 read as 亿) run only on text
  known to be in that language. The audit and repair number checks pass the
  pair, so an English "five cent" is never 100. Lone `un`/`une`/`neuf`
  (article, "new") are not counts.
- **Style sheet.** Two address forms keep the "X or the polite Y" sentence;
  more (Japanese honorifics) are listed.
- **Tested.** `tests/test_language_profiles.py`:
  - profile completeness and the French number round trips;
  - French numbers only in French text, and Japanese 億;
  - the French and Japanese conventions;
  - style-sheet wording, elision, and the same-script untranslated check.
  `en>fr` joins the end-to-end runs. Backend 1,134, frontend 91.
- **Benchmarks** (`lang_benchmark/run-benchmarks.ps1`, results in
  `lang_benchmark/results/language-results.md`):

  | Run | Book | Pair |
  |---|---|---|
  | bench-lang-ja-zh | 羅生門 (story only) | `ja>zh` |
  | bench-lang-en-fr | Alice chapters 1–4 | `en>fr` |
  | bench-lang-en-ja | Alice chapters 1–4 | `en>ja` |
  | bench-lang-fr-en | La chasse au météore chapters I–III | `fr>en` |

  羅生門 arrives as one 6,095-character segment, a test of the splitting.
- **Known limits.**
  - The token estimate counts kana as UTF-8 bytes, not one token each,
    because the tuned set is frozen. It underestimates Japanese by about a
    quarter; the budget's reserve covers it.
  - The optional quantity audit (`quantities.py`) reads English and Chinese
    only.

**Phase 5 benchmark results (2026-10-02).** Unattended runs with qwen3.8 for
translation and gemma4 for the audit; details in
`lang_benchmark/results/language-results.md`.

| Run | Time | Review queue | Outcome |
|---|---|---|---|
| `ja>zh` 羅生門 | 2.4 min | 0 | Complete. The single 6,095-character story segment translated whole; glossary of 15 entries in `ja>zh` (羅生門 → 罗生门); style sheet 下人 (他, 你). |
| `en>fr` Alice 1–4 | 22.7 min | 2 | « » in 114 segments; tu/vous chosen per character. One French grammar slip and calques caught by the semantic audit. Pat's dialect lines **left in English**, missed by every check (fixed below). |
| `en>ja` Alice 1–4 | 21.0 min | 1 | 「」 in 114 segments; a repeated sentence caught as duplication; a stray "splash" caught and repaired. One glossary review looped for 48k characters before a retry (8.7 min; fixed below). |
| `fr>en` Météore I–III | 16.6 min | 1 | Fluent; literal time formats ("nine o'clock thirty") caught by the semantic audit. 15 false glossary findings and two short lines refused by translation (fixed below). |

Fixed from the benchmarks (all with tests):

1. **Same-script leftovers.** The doc assumed the semantic audit would catch
   untranslated text when two languages share a script; it did not. Profiles
   now carry `function_words`. In a pair sharing a script, a sentence or
   quoted span with at least two of the source's function words and none of
   the target's is "possible untranslated … passage". On the benchmark
   output it finds Pat's three lines and nothing in the other 482 segments.
2. **Identical short lines.** The translate stage refused "DEAN FORSYTH."
   because it matched the source. `copy_is_untranslated()` now gives the
   translate stage and the audit one rule: always when the scripts differ,
   from 20 letters when they share one.
3. **Abbreviations and plurals.** The glossary term "Mr" matched "Mrs"
   through the plural ending, so ten "Mr." findings were false. A plural
   ending now needs a word of three letters or more (English too).
4. **French nested quotes.** “ ” inside « » is the French nested quote, not a
   slip (`ConventionRule.nested_ok`). The one finding was a false positive
   and sent a segment to review.
5. **French number words.**
   - A number never runs across an inline marker ("six *cents*" is coins).
   - "un bon mille" is a mile.
   - A bare plural (cents, millions) is not a number.
   - Times of day go to the model.
   The layer then turns two false mismatches on the book into matches and
   adds none.
6. **Runaway glossary review.** Approval calls are capped at 400 output
   tokens per case plus 1,024.

Left as is:
- One repair wrote the superscript "Dr" as `Dr <I000>r</I000>`, a model slip
  on an inline marker.
- Style-sheet "expressions" sometimes pick ordinary words (rendez-vous);
  these are low findings, listed only, as for en-zh.
- The French typographic no-break space was never used by the model (121
  segments with an ordinary space). The majority rule correctly stays quiet.
  A compile-time typographic pass could add it, if wanted.

**Phase 6 status (2026-10-03): profiles done and benchmarked (results below).**
German replaced Russian (section 5).

- **Spanish (`es`).** « » or “ ” by the book's majority; ¿ and ¡ (a question
  or exclamation without its opening mark is a departure); raya dialogue;
  él/ella, tú/usted. Number words: "y" joins only tens and units, -cientos,
  mil, millón. A lone un/una and times of day ("a las cinco") are not counts.
- **German (`de`).** „ “ or » « by majority; an English closing ” is a slip;
  er/sie/es, du/Sie. Number words are one word (dreihundertfünfundzwanzig),
  parsed by parts, with Million/Milliarde as words and ß kept (casefold
  would make dreissig). Case endings
  (`inflection_suffix`): an approved term "Käfer" counts in "des Käfers".
- **Korean (`ko`).** “ ” quotes; 그/그녀. Address is a speech level, not a
  pronoun pair, so the style sheet keeps pronoun and voice only. Particles
  attach to words (`attached_particles`, the list of them), so "김첨지" is
  found in "김첨지는" and "김첨지에게는", and not inside another word.
  Numbers are read only before a counter (두 사람, 삼십 전, 일 원 오십 전):
  alone, the syllables are ordinary words (일 "work", 열 "will open").
- **Stem matching.** `inflection_suffix` lets a target term carry its
  language's endings in the glossary audit and the repair check (German case
  endings; Spanish and French plurals). The term must still start and end on
  a word boundary. English has none, so en-zh and zh-en are unchanged.
- **Shared-script leftovers.** The check counts only function words that
  belong to the source and not the target: German "was" and Spanish "he"
  ("I have") are not English evidence.
- **Number registry.** `number_words.OBJECTIVE_PATTERNS` and `WORD_VALUES` by
  parser name. `content_policy` runs the one the language's profile names.
- **Tested.** `tests/test_language_profiles.py` covers:
  - the Spanish, German, and Korean number round trips;
  - cross-language number comparisons;
  - the ¿ ¡ and German quote conventions;
  - endings in term compliance, Korean particles, and English left in
    German.
  The tests that used German or Korean as the generic example now use
  Italian, Russian, and Thai.
- **Benchmark material.**
  - `scripts/text_to_epub.py` turns hard-wrapped plain text into an EPUB.
    The Korean samples are .txt, and 운수 좋은 날 wraps at a fixed width,
    mostly inside words, hence `--join ""`.
  - `scripts/create_epub_section_subset.py` keeps the chapters between two
    headings of one spine document.
  - Runs with `lang_benchmark/run-benchmarks.ps1`, results in
    `lang_benchmark/results/phase6-results.md`:

  | Run | Book | Pair |
  |---|---|---|
  | bench-lang-ko-zh | 운수 좋은 날 (10k characters) | `ko>zh` |
  | bench-lang-de-en | Die Verwandlung, part I (39k) | `de>en` |
  | bench-lang-de-zh | Die Verwandlung, part I | `de>zh` |
  | bench-lang-en-es | Alice chapters 1–4 | `en>es` |
  | bench-lang-en-de | Alice chapters 1–4 | `en>de` |
  | bench-lang-en-ko | Alice chapters 1–4 | `en>ko` |
  | bench-lang-es-en | Fortunata y Jacinta, part one, chapters I–II (92k) | `es>en` |
  | bench-lang-es-zh | Fortunata y Jacinta, part one, chapters I–II | `es>zh` |

- **Not benchmarked.** 홍길동전 (Wanpan edition) is written in archaic
  Korean with old combining jamo (ᄒᆞ), about a quarter of its characters. It
  would test the model on Middle Korean rather than the Korean profile.

**Phase 6 benchmark results (2026-10-03).** Unattended runs, same models as
phase 5; details in `lang_benchmark/results/phase6-results.md` and one
`review-<job>.txt` per run beside it (`inspect_runs.py`).

| Run | Time | Review queue | Outcome |
|---|---|---|---|
| `ko>zh` 운수 좋은 날 | 40.9 min | 0 | Complete; “ ” and —— throughout. 83 glossary entries, nine of them one-syllable everyday words (죽 → 粥): 16 of the 29 glossary findings came from them, 12 by matching inside another word (fixed below). One translate call took 24.8 min. |
| `de>en` Verwandlung I | 12.7 min | 0 | Fluent. "Prokurist" left in German in all 15 segments against the glossary (fixed below). German clock times misread ("einviertel acht"), caught by the semantic audit. |
| `de>zh` Verwandlung I | 12.8 min | 1 | The same "Prokurist" conflict (经理 for 副经理). "halb sieben" became 七点半 and no check saw it. |
| `en>es` Alice 1–4 | 26.9 min | 14 | « » in 114 segments. 22 missing ¿ ¡ found, 5 of them false (fixed below); 8 real ones were still open after repair and went to review. The glossary held "well → pozo", and a repair wrote «Pozo, no tiene nada que hacer ahí» (fixed below). |
| `en>de` Alice 1–4 | 59.5 min | 16 | „ “ in 113 segments. Chapter IV dialogue left in English: 14 passages found by the left-over check and repaired. 32 quotations closed with ”; the repair model returned the same mark for 10 (fixed below). "der Weiße Hase" was flagged and repaired to "der Weißer Hase" (fixed below). Audit 47 min. |
| `en>ko` Alice 1–4 | 16.3 min | 4 | “ ” in 113 segments. Seven Chinese words in the Korean (老同志, 着手), none found by a rule (fixed below). Literal prose; 17 semantic findings. |
| `es>en` Fortunata I–II | (resumed) | 2 | Readable, literal with idioms (28 semantic findings). Year ranges ("del 40 al 45") flagged by the numeric check, rightly. |
| `es>zh` Fortunata I–II | 36.2 min | 6 | 135 glossary entries. Two fabric names left in Spanish, found. "D." (don) rendered 多尼奥 throughout. |

Fixed from the benchmarks (all with tests):

1. **Korean particles.** `attached_particles` was a flag that dropped the
   right-hand word boundary, so 죽 (porridge) matched 죽었다 (died) and 간
   (liver) matched 간신히. It is now the list of particles: a term matches at
   the start of a word and is followed by up to three of them or nothing.
2. **Inflection inside a term.** Every word of a German, Spanish, or French
   term may take the language's endings, and a word before the last may change
   the one it has: the approved "Weißer Hase" is met by "der Weiße Hase" and
   "des Weißen Hasen" (`LanguageProfile.inflected`), on the source side too.
3. **German ” without a model.** `ConventionRule.replace` names a slip that is
   one wrong character. Repair corrects it mechanically, alone or in the
   model's answer for a segment with other findings. On the benchmark output
   it corrects all 32.
4. **Spanish ¿ ¡.** A sentence opened by either mark is opened (the Academy
   accepts ¿…! and ¡…?). A dash, an ellipsis, or the quotation mark before a
   dialogue tag does not end the sentence. A passage copied from the source
   ("Où est ma chatte?") keeps its own punctuation: before, that finding made
   the repair turn the sentence beside it into a question.
5. **A third script.** For pairs other than en/zh, letters of a script that
   neither language is written in and the source does not contain are
   "text in a script neither language uses". It finds the seven Chinese words
   in the Korean Alice and nothing in the other seven runs.
6. **Style sheet against glossary.** The style sheet rendered "Herr Prokurist"
   as "the Prokurist" while the glossary said "Procurator", and the translator
   followed the style sheet. An unreviewed style expression that contains an
   approved term without its rendering is now dropped at approval
   (`drop_glossary_conflicts`); a person's reviewed sheet stands. This applies
   to en/zh too.
7. **Ordinary words in the glossary.** The generic-term screen read English
   only. For other pairs it now follows the source language: a lowercase word
   in a language that capitalizes names is no name whatever its category
   ("well" filed as a place), a title (señor) excepted; a one-syllable Korean
   word is an everyday one. Dropped at approval and not enforced by the audit,
   as for en/zh. German nouns are all capitalized, so the screen cannot tell;
   its extraction prompt says so instead (`extraction_note`).

Left as is:
- German and Spanish clock times ("halb sieben" is half past six) are the
  model's to get right; the number layer skips times of day.
- "the Father", "the Mother" in the English Verwandlung came from glossary
  entries for Vater and Mutter; the extraction note now discourages them.
- The semantic auditor sometimes demands a glossary term where the word has
  another sense; the screen in 7 removes the cases seen.
- A book in a language the model handles poorly (Korean here) stays literal:
  the notice in the interface says so.

**Model comparison (2026-10-03): translategemma:27b as the translator.** The
eight pairs without Chinese were run again with `translation.model:
translategemma:27b` (new setting: the translate stage only), `gemma4:31b` as
primary and repair model, and qwen3.8 as fallback. Details in
`lang_benchmark/results/translategemma-results.md` and
`translategemma-compare.txt`.

- **Prose.** On reading the samples, better than qwen3.8 in every target:
  idiomatic German and Spanish, natural Japanese, Korean without Chinese
  words, and no dialogue left in English. Semantic findings fell in five
  pairs (German 20 to 7, es>en 28 to 9), stayed at 9 in French, and rose in
  Korean (17 to 21) and Japanese (8 to 16), where they include the repeated
  passages below.
- **Repeated passages.** In a long chunk translategemma loses its place and
  repeats earlier passages under later markers: 83 passages in the eight runs
  (16 of 31 in Die Verwandlung, sent as one 11,000-token chunk). The translate
  stage accepted them, and after audit and repair some stood in the final text
  outside the review queue. The translation contract now refuses a passage
  that opens like an earlier one with a different source (`repeated_passage`),
  so it is retried or goes to the fallback model. On the saved output the
  check finds the 83 and nothing in the twelve qwen runs.
- **Not a general model.** Asked to resolve six glossary terms, translategemma
  returned one; fallback harmonization, a rewrite task, set off the same
  repetition. It translates only.
- **gemma4:31b as primary.** Its glossary decisions were sound, but the
  structured resolution call ran at about 1 token a second: 15.6 and 48
  minutes for single calls. qwen3.8 stays the primary model.
- **An empty extraction.** On Fortunata, qwen returned 20 characters and no
  glossary entry in each chunk, and the book ran without a glossary.
  Extraction now asks again when an answer names three or more characters and
  no entry.
- **Second-round configs** (`lang_benchmark/configs/tg-*.yaml`): translategemma
  translating in chunks of 8,000 prompt tokens, no fallback harmonization,
  qwen3.8 for the glossary, gemma4:31b for repair.
- **Second round** (de>en, en>ko, es>en; `translategemma2-compare.txt`). No
  exact repeats in any first draft, and es>en got its glossary back (168
  entries after the extraction retry). Three more gaps showed:
  - *A fatal merge.* en>de stopped with "internal merged translation failed
    validation": a retried passage repeated one accepted earlier, which only
    the merged chunk shows. That is now a retry, not an error.
  - *Shifted passages.* In en>ko a passage held a fresh translation of its
    neighbour, so nothing was repeated word for word; about seven reached the
    final text, some outside the review queue. `misplaced_passages` names two
    nearby translations that are closely alike while their sources are not
    (both, since the text cannot say which is wrong), in the translation
    contract and in the audit ("translation closely matches that of a
    different source segment"). On the saved output it finds them and nothing
    in the twelve qwen runs.
  - *Left in the source language.* In de>en two passages came back in German
    with the spelling modernized, so not identical to the source; repair
    could not translate them and they went to review. The translation
    contract now refuses a passage that reads as the source language
    (`reads_as_source`: four of its function words and more than three for
    each of the target's).
- **Third round, with those checks** (en>de resumed; en>ko and de>en as new
  jobs; `translategemma3-compare.txt`). No repeated, shifted, or
  source-language passage in any first draft or final text.

  | Run | Time | Review queue | Against the qwen run |
  |---|---|---|---|
  | `de>en` | 20.8 min | 0 of 31, complete | 12.7 min, 0 |
  | `en>de` | 23.2 min | 1 of 145 | 59.5 min, 16 |
  | `en>ko` | 39.5 min | 4 of 145 | 16.3 min, 4 |

  The contract failures send passages to the fallback model: 12 of 139
  longer passages in en>de and 27 of 125 in en>ko are qwen's wording. In
  Korean that brought two Chinese words back, which the third-script check
  found and repair removed.
- **The other five pairs** (en>es, en>fr, en>ja, fr>en, es>en) then ran with
  the third-round code as `bench-tg3-*`: no repeated, shifted, or
  source-language passage in any of them; en>fr, en>ja, and fr>en completed
  with an empty review queue. Section 7 rates every pair.

- **A finding has a code.** Repair decided to translate a passage again, and
  not edit it, by matching the wording of the audit's finding. A reworded or
  translated message would have ended that without an error. The seven
  findings concerned now carry `AuditIssue.code` (`no_target_text`,
  `identical_to_source`, `duplicates_other_segment`, `matches_other_segment`,
  `shifted_translation`, `wrong_passage_terms`, `unusual_length`) and repair
  reads the code (`whole_translation_is_wrong`); the wording is read only
  for an audit written before codes. The field is left out of stored
  findings that have none and out of the schema the auditor model is sent,
  so stage hashes and prompts are unchanged. Other findings have no code
  yet: giving each one is the first step of translating the interface.

## 7. What the models and the pipeline can do

Written 2026-10-04 from the benchmark runs of phases 5 and 6 and the model
comparison (section 6). It answers three questions: is a local model good
enough for a pair, which model should do which job, and what the pipeline
itself guarantees or cannot do.

### 7.1 The evidence, and how far it goes

- **39 runs** over 16 pairs, on seven public-domain books (the seventh,
  阿Q正传, and its eight runs are in 7.7): Alice chapters 1–4
  (45,000 characters), Die Verwandlung part I (39,000), La Chasse au météore
  chapters I–III (65,000), Fortunata y Jacinta part one I–II (92,000), 羅生門
  (7,000) and 운수 좋은 날 (10,000). The one-line figures for every run are
  in `lang_benchmark/results/capability-summary.txt` (`summarize.py` writes
  the same line for any run).
- **One excerpt per pair, literary prose only.** No technical, legal, or
  modern conversational text was run.
- **No reference scoring.** The ratings below come from reading samples of
  each run and from the audit's findings. The semantic auditor is a model
  too: it misses errors and reports matters of taste, so its counts compare
  two runs of one pair, not two pairs.
- **Local models at one quantization**, on one machine; the times are that
  machine's.
- The eight pairs without Chinese were run with both translators. The four
  pairs into Chinese were run with qwen3.8 only; translategemma was not tried
  into Chinese. The four pairs out of Chinese were run with both (7.7).

### 7.2 Pair by pair

The rating is for the best setup tested, unattended, before a person reviews
anything:

- **A**: a draft a bilingual reader can finish with light review.
- **B**: readable and mostly right, but stiff or literal; needs an editing
  pass throughout.
- **C**: errors of meaning in ordinary sentences; not usable without a
  translator reworking it.

| Pair | Translator | Rating | Time | Review queue | Semantic findings (high) | What the reading showed |
|---|---|---|---|---|---|---|
| `en>zh`, `zh>en` | qwen3.8 | A | — | — | — | The tuned pairs; benchmarked separately, with the prose rewrite stage. |
| `ja>zh` | qwen3.8 | A | 2 min | 0 of 18 | 0 | Fluent. A short story: the smallest sample here. |
| `fr>en` | translategemma | A | 28 min | 0 of 340 | 18 (5) | Fluent with either translator; qwen3.8 took 17 min and left 1 passage for review. |
| `de>en` | translategemma | A | 21 min | 0 of 31 | 11 (2) | Natural English. qwen3.8 kept German word order ("One understood, therefore, his words no longer"). Clock times ("einviertel acht") are misread by both. |
| `en>fr` | translategemma | A | 43 min | 0 of 145 | 11 (0) | « » throughout, idiomatic. The no-break space before ; : ! ? is never written. |
| `en>de` | translategemma | A− | 23 min | 1 of 145 | 8 (4) | Idiomatic, „ “ correct. qwen3.8 is C here: gender slips, an invented word, a chapter's dialogue left in English, 16 passages for review. |
| `en>es` | translategemma | B+ | 34 min | 2 of 145 | 14 (2) | Reads well; short lines sometimes wrong ("Not I!" as "¡No, yo!"). It writes “ ” where qwen3.8 wrote « ». |
| `es>en` | translategemma | B+ | 45 min | 3 of 88 | 14 (3) | Clear English. Nineteenth-century idiom and year ranges ("del 40 al 45") still go wrong. qwen3.8 is B: idioms word for word. |
| `en>ja` | translategemma | B | 38 min | 0 of 145 | 17 (9) | Natural sentences, 「」 correct, but register wanders within a speech and some lines are invented or dropped. qwen3.8 is also B: stiffer, fewer findings (8). |
| `de>zh`, `es>zh` | qwen3.8 | B | 13 / 36 min | 1 of 31 / 6 of 88 | 2 / 17 (0 / 1) | Understandable and flat; long sentences cut short, idioms literal (吐露了栗子). "halb sieben" became 七点半. |
| `ko>zh` | qwen3.8 | B− | 41 min | 0 of 103 | 7 (2) | Readable, with errors of meaning in period idiom. |
| `en>ko` | translategemma | C+ | 40 min | 4 of 145 | 17 (10) | Better than qwen3.8 (C: Chinese words in the Korean, wrong sound words), but still mistranslates plain sentences ("sister" as "younger sister") and overuses 그녀. |
| `zh>es` | translategemma | B+ | 38 min | 10 of 163 | 16 (7) | Fuller sentences than qwen3.8 (B), which is complete and literal. One clean run (7.7). |
| `zh>fr` | qwen3.8 | B | 20 min | 16 of 163 | 26 (7) | Complete and literal, tenses mixed. translategemma is smoother and shortens. |
| `zh>de` | translategemma | B | 73 min* | 13 of 163 | 58 (14)* | Better German than qwen3.8 (B−), but it shortens and misreads now and then. One clean run (7.7). |
| `zh>ja` | translategemma | B, not unattended | 79 min* | 2 of 163 | 42 (22)* | Natural Japanese. A wrong passage passed every check for four rounds; a reader of Japanese must compare each passage with the Chinese. qwen3.8 is C: Chinese clauses left in the Japanese. |

**Reading the times and counts.** The rows marked * were run after the audit
model began reading every prose segment of a pair other than en/zh (7.7,
fixes 5 and 6); the others before, when it read only the segments a rule
flagged, long ones, and ones with numbers, at most 50 a chapter. For those:

- **Times are low.** The audit was the largest part already; with every
  segment read it roughly doubles a run. The two Chinese-source pairs that ran
  both ways went from 46 to 73 minutes (`zh>de`) and from 45 to 79 (`zh>ja`).
- **"Semantic findings" are counts over the segments shown**, not over the
  text, and a run with many short lines of dialogue was the least examined. A
  rerun would report more findings and send more segments to review.
- **The ratings stand**: they rest on reading the passages.

The four Chinese-source rows are for 阿Q正传, a harder text than the others
(7.7).

A pair not listed is untested. For a language with a profile (section 2.4)
the checks run; for any other language most of them are skipped (section
2.3), and the model is on its own.

### 7.3 Model by job

| Model | Translating | Glossary (extract, resolve, approve) | Semantic audit | Repair |
|---|---|---|---|---|
| qwen3.8 (18 GB) | Best into and out of Chinese. Fair into English. Weak into German and Korean: leaves source text, mixes in Chinese. Keeps the marker contract almost always. | **Use it.** About a minute a call. Once returned no entries for a whole book (now asked again). Collects ordinary words (section 6, phase 6). | Not tested. | Works and is fast; writes its own weaker German or Korean into the fix. |
| translategemma:27b (17 GB) | **Best prose in every pair without Chinese.** But: drops inline markers in most chunks, and in long chunks repeats or shifts passages (caught since round 3). Needs chunks of 8,000 prompt tokens and a fallback model. Its translate stage takes 3 to 4 times as long as qwen3.8's because of the retries. | **Cannot.** Returned 1 decision for 6 terms. | Not tested. | Not tested; it loops on rewrite tasks (fallback harmonization). |
| gemma4:31b (20 GB) | Not tested. | Sound decisions, but about 1 token a second on the structured call: 16 and 48 minutes for single calls. **Do not use.** | **Use it.** Finds real errors (clock times, calques, untranslated lines); also reports taste, and misses some. The slowest stage: 5 to 47 minutes a book. | Works. Slow on the structured edit (1 to 6 minutes a book, 26 at worst). |
| gemma4:26b (19 GB) | Not tested. | Not tested. | Not tested. | Verifies repairs in every run here; no problem seen. |

Other installed models (mistral-small3.2, glm-4.7-flash, deepseek-r1,
and so on) were not run through the pipeline.

### 7.4 Recommended setups

| For | `translation.model` | `ollama.model` (glossary) | Fallback | Audit / repair | Other settings |
|---|---|---|---|---|---|
| `en>zh`, `zh>en` | (primary) | qwen3.8 | — | gemma4:31b / qwen3.8 | The shipped defaults; prose rewrite on. |
| Any other pair into Chinese (`ja`, `ko`, `de`, `es` tested) | (primary) | qwen3.8 | — | gemma4:31b / qwen3.8 | Prose rewrite stays off. Expect B. |
| French, German, Spanish to or from English | translategemma:27b | qwen3.8 | qwen3.8 | gemma4:31b / gemma4:31b | `max_prompt_tokens: 8000`, `harmonize_fallback_with_primary: false`. |
| `en>ja` | translategemma:27b, or qwen3.8 for a stiffer, safer text | qwen3.8 | qwen3.8 | gemma4:31b / gemma4:31b | As above. A reviewer who reads Japanese. |
| `en>ko` | translategemma:27b | qwen3.8 | qwen3.8 | gemma4:31b / gemma4:31b | As above. Only with a translator to rework it. |
| `zh>es`, `zh>de` | translategemma:27b | qwen3.8 | qwen3.8 | gemma4:31b / gemma4:31b | As the row for French, German, Spanish. |
| `zh>fr` | (primary) | qwen3.8 | — | gemma4:31b / qwen3.8 | translategemma shortens here. |
| `zh>ja` | translategemma:27b | qwen3.8 | qwen3.8 | gemma4:31b / gemma4:31b | As above. Only with a reader of Japanese checking each passage. |
| A pair between two languages other than English and Chinese | untested | | | | Try one chapter first (section 2.8). |

The configs in `lang_benchmark/configs/tg-*.yaml` are the translategemma
rows written out, and `qwen-*.yaml` the others.

With translategemma, part of the book is the fallback model's wording: the
passages it could not return in a valid form. In the last round that was
about 3 of 30 longer passages in `de>en`, 12 of 139 in `en>de`, 29 of 138 in
`en>es`, 27 of 125 in `en>ko`, and up to a third in `en>fr`. Where the
fallback is weak in the target language (German, Korean), those passages are
the weaker ones.

### 7.5 What the pipeline does, whatever the model

Checked by rule, for every pair:

- **Structure.** Every segment comes back under its own marker, inline
  markers in order, nothing empty.
- **Placement.** A passage repeated under a later marker, translated under
  its neighbour's, or one of a run of passages shifted by some markers
  (found by length and dialogue shape) fails the translation contract and is
  flagged again in the audit.
- **Untranslated text.** By script when the scripts differ; by function words
  when they do not (a passage, or a whole segment, still in the source
  language); between Chinese and Japanese, a run of six characters copied
  from the source, or a Japanese text with no kana; letters of a third
  script.
- **A second reading.** For every pair but en/zh, the audit model compares
  each prose segment with its source (7.7). For en/zh it reads the segments
  the rules mark as risky.
- **Glossary.** One rendering per approved term, with the target language's
  endings (German, Spanish, French) and the source's particles (Korean). The
  style sheet may not contradict it.
- **Numbers.** Digits everywhere; number words in English, Chinese, French,
  Japanese, Spanish, German, and Korean.
- **Conventions.** Quotation marks and the marks particular to a language
  (¿ ¡, the German closing quote), by the book's own majority, for French,
  Japanese, Spanish, German, Korean, and Chinese.
- **Recovery.** A failed passage is retried, then sent to a fallback model,
  then left for a person: a run does not stop on one bad passage.

Left to the models, and so only as good as they are:

- Meaning, idiom, register, and humour. The semantic audit catches part of
  it and sends the rest to the review queue only when it notices.
- Times of day and other numbers that are not counts ("halb sieben").
- Which words belong in the glossary. A wrong entry is enforced like a right
  one; the glossary review is where a person stops it.

### 7.6 Limits

- **Not a finished translation.** In no pair did an unattended run produce
  text to publish. An A is a draft worth a reviewer's time.
- **Korean as a target is not there** with these models, and the checks
  cannot make it so: they find misplaced and untranslated text, not a
  sentence that is fluent and wrong.
- **Languages without a profile** run with the script check, digits, and the
  semantic audit only (section 2.3). None was benchmarked with a model.
  `tests/test_language_generic_tier.py` runs the model-free checks on sixteen
  of them (Arabic, Persian, Hebrew, Hindi, Bengali, Thai, Khmer, Greek,
  Georgian, Armenian, Amharic, Russian, Serbian, Vietnamese, Turkish,
  Polish), each with English both ways and with Russian. What that sweep
  changed:
  - *Source text left in a same-script translation was not looked for* where
    either language lacks a list of function words (English into Polish,
    Turkish, Vietnamese). It is now found by the words of the source passage
    itself: a sentence of six or more words, four in five of them the
    source's, or six of the source's words in a row. Names (words written
    with a capital) do not count. Over the 1,194 passages of seven en/de/fr/es
    benchmark runs the rule fired twice, both times on a line of French or
    German the English book quotes and the translation rightly keeps; such a
    line is a medium finding to dismiss.
  - *Twelve scripts were unknown* (Ethiopic, Khmer, Lao, Myanmar, Tibetan,
    Tamil, Telugu, Kannada, Malayalam, Gujarati, Gurmukhi, Sinhala), so a
    translation into Amharic or Khmer left in English passed. They and
    their languages are in the table now. A language still outside it is
    accepted, and the rule above covers it.
  - Still a limit: a glossary term is matched as written. A language that
    inflects names (Georgian ალისა, ალისამ; Russian, Polish, Turkish,
    Finnish) needs each form as an alias, or a profile with its endings as
    German and Korean have.
- **Pairs between two languages other than English and Chinese** were not
  run, apart from the four into Chinese.
- **Tuned for Chinese only:** the prose rewrite, the character section of the
  consistency report, and the optional quantity audit (English and Chinese).
- **The book's title and contents are translated by their own stage, and
  kept as they were if no model answers.** The compiled EPUB is tagged with
  the target language (`dc:language`, and `lang` on each translated
  document; a phrase marked as some third language keeps its mark), and its
  title, document titles, and table of contents are given in translation
  (`localize_package`). One that reads the same as a translated passage (a
  title page's heading, a chapter's heading) takes that passage's
  translation. The rest are settled by a stage before the compile,
  `translate_title`. The title: the config's `translation.translated_title`
  when set, else one short model call with the glossary. A title settled so
  (or corrected on the Text tab) is the title wherever the package has it,
  whatever a passage that reads the same was translated as (the compile's
  hash says where a settled title is also a passage, so that a book compiled
  when the title gave way to it is compiled again, and no other is). The contents
  entries and document titles that are no passage ("Chapter 7" for a heading
  "CHAPTER VII. A Mad Tea-Party"): batches of 40 to a call, each with the
  glossary and the book's headings as the book translated them, answered as
  a numbered list; a line that is not numbered as an entry is dropped. An
  entry that is only a number is left alone. The book's footnotes and
  endnotes are settled here too (an EPUB's, marked `epub:type` footnote,
  endnote, rearnote, note or role `doc-footnote`/`doc-endnote`, and a Word
  or Markdown book's), kept apart from the passages by the extractor, in
  batches of 20 paragraphs (or about 4,000 characters) to a call, with their
  links and emphasis as inline markers; a paragraph answered without its
  markers in order stays as it was. The compile puts each back by its
  element path, as it does a passage, and the validate stage checks it. The
  pictures' descriptions (alt text) are settled here as well. A job whose
  EPUB has notes is decompiled again once, its notes out of its passages,
  which renumbers its passages and so translates it again. The stage's calls are logged and counted as every
  stage's are. If no model answers, the book
  keeps its title and those entries, and the stage says how many it
  translated and how to set a title. The validate stage accepts those
  changes to the package document and the contents, and no others. A job
  compiled before the stage existed reads as past it.
- **Token estimates undercount kana**, so a Japanese source may be chunked
  larger than planned.
- **The glossary is the weakest stage.** Extraction collects ordinary words
  and, in German, common nouns; the screen in phase 6 removes the plain cases
  only. Unattended approval is for benchmarks; a real book wants the human
  review the default config requires.
- **Cost.** Measured before the audit model read every segment, a
  45,000-character excerpt took 23 to 43 minutes with translategemma against
  16 to 60 with qwen3.8. With every segment read, expect about twice that for
  a pair other than en/zh: the audit is most of a run (41 to 47 of 77 to 82
  minutes on 阿Q正传 into Japanese). en/zh is unchanged.
- **The evidence is thin** by design of a benchmark: one excerpt, one run,
  one reader per pair. A second book in a pair may move its rating a step.

### 7.7 Chinese as the source

Added 2026-10-04. 阿Q正传 chapters 1–5 (Lu Xun, 1921–22; 10,900 characters,
163 segments; text from Chinese Wikisource) into French, German, Spanish, and
Japanese, each with both translators: eight runs, `bench-zhsrc-*`, figures in
`lang_benchmark/results/zh-source-summary.txt`, side-by-side passages in
`zhsrc-samples-*.txt` beside it. The text is hard: 1920s vernacular, irony,
and classical quotation. The ratings are for hard Chinese, not for a
present-day novel.

| Pair | qwen3.8 | translategemma:27b | Use |
|---|---|---|---|
| `zh>fr` | **B.** Complete and literal; tenses mixed (passé composé in narrative). 20 min, 16 of 163 for review. | **B.** Smoother, but shortens: a paragraph of classical quotation became two sentences. No shifted passages. 42 min, 22 for review. | qwen3.8: nothing is left out. |
| `zh>es` | **B.** Complete, readable, literal. 21 min, 9 for review. | **B+** where in place: fuller sentences, quotation marks kept. But 16 passages shifted in the first draft, 5 still wrong in the final text. 43 min, 16 for review. | qwen3.8 as run. translategemma once it has run clean with the checks below. |
| `zh>de` | **B−.** Complete; invented words ("Großbüßigen") and wrong verbs ("ein Boot stützen"). 20 min, 14 for review. | Not usable as run: 31 passages shifted in the first draft, 13 still wrong in the final text, 8 of them outside the review queue. Where in place, better German. 46 min, 21 for review. | qwen3.8 as run. |
| `zh>ja` | **C.** Chinese clauses left standing inside the Japanese (抢进去就是一拳) in 32 of 163 segments, and only 2 segments went to review. 14 min. | **B** where in place: natural Japanese. But 26 passages shifted in the first draft; at least 2 wrong in the final text, and the run reported itself complete. 45 min. | Neither as run. translategemma once it has run clean with the checks below. |

What the round showed:

- **translategemma shifts passages when the source is Chinese.** With many
  short lines of dialogue it skips one and writes every following
  translation one or several markers late. Nothing is repeated, so the
  checks of section 6 saw nothing; the audit model caught some, and the rest
  went into the final text under the wrong segments.
- **qwen3.8 cannot be trusted into Japanese from Chinese.** It keeps the
  Chinese wording wherever the characters look Japanese enough. The two
  languages share a script, so no check noticed.
- **translategemma abridges** dense passages. The audit reports some as
  omissions; a reader of the target alone would not know.
- **Glossaries are large and loosely kept.** 98 to 151 entries for 10,900
  characters, and translategemma departed from them 78 to 109 times a run.

Fixed from this round (with tests; replayed on all 49 saved runs):

1. **Shifted passages.** `shifted_passages` compares, over five passages at
   a time, each translation's length and whether it opens as dialogue with
   its own source and with the sources up to eight markers away. A window
   that fits a shifted pairing well, and far better than the straight one, is
   named. It fails the translation contract (`shifted_passage`, so the
   passages are retried or go to the fallback model) and is a high finding in
   the audit. On the saved output: 31, 11, and 26 passages in the German,
   Spanish, and Japanese drafts above; nothing in any qwen3.8 run or in the
   third-round translategemma runs; 36 of 49 runs untouched.
2. **Chinese left in Japanese.** Between two unspaced languages sharing a
   script, a run of six characters copied from the source is "possible
   untranslated text", in the audit and in the translation contract
   (`copied_source_run`). It finds the 32 segments above and nothing in the
   other Japanese or Chinese-target runs.

Not yet shown by a run: that translategemma, with these two checks sending
its shifted passages to retry and fallback, gives a clean `zh>de`, `zh>es`,
or `zh>ja`. Until it has, the table's "Use" column stands: **qwen3.8 for a
Chinese source into French, Spanish, and German; no tested setup for
Japanese.**

A single shifted passage, alone among passages in place, is still invisible
to the rules: a window of five is what the shape check needs. The semantic
audit is the only guard there.

## 8. Later: a cloud model behind the same pipeline

A thought recorded on 2026-10-04, not planned work.

Section 7 puts the limit of this pipeline at the local models, not at the
stages. A hosted model with a larger context and better translation would
lift the ratings most where they are lowest (Korean as a target, Chinese into
Japanese).

- **What it would be here.** A second client beside the Ollama one, chosen
  per role in the config the way `translation.model` chooses a model. The
  stages, the glossary, the contract checks, the audit, and the review queue
  stay as they are: they do not depend on which model answers.
- **First experiment.** One role only (the translator, or the semantic
  audit), on the benchmark excerpts of section 7, against the tables there.
- **What it needs deciding first.**
  - Sending a book's text to a third party: an explicit choice per job,
    never a default, and not for a book whose rights do not allow it.
  - Cost and rate limits: a budget and a report the local runs never needed.
  - Reproducibility: a hosted model changes without notice, so what a
    checkpoint hash means for it.
  - The client's Ollama-specific parts (context sizing, model loading,
    structured output) have not been sized.
- **What it is not.** A pipeline designed around a very large context
  (a chapter or a book in one call, translation and audit together, little
  chunking or fallback) is a different design and belongs in its own
  document, once the experiment above shows where this pipeline is the
  bottleneck.

**Second round (2026-10-04, `bench-zhsrc2-tg-*`): translategemma again, with
the two checks.**

| Pair | Outcome |
|---|---|
| `zh>es` | Clean by every rule and by comparison with the qwen3.8 run: the 4 shifted passages of the first draft were retried and replaced. 38 min, 10 of 163 for review. Reads better than qwen3.8 ("¡A-Q, mocoso!"). |
| `zh>ja` | The shifted runs were caught and retried, but the final text still held three wrong passages with one segment in the review queue: one left wholly in Chinese (「和尚动得，我动不得？」他扭住她的面颊。) and two short lines carrying another passage's translation. |
| `zh>de` | Stopped in translate with "internal merged translation failed validation": the merge accepted a late repeat as a retry but not a late shift. |

Three more fixes from it:

3. **A shift found at the merge is a retry too**, like a repeat.
4. **No kana, no Japanese.** A translation of ten or more characters written
   only in the script the two languages share, with none of the target's own
   (`lacks_target_script`), is "contains no Japanese text": a high finding
   and a contract failure. It finds the passage above and nothing in the
   other six Japanese runs.
5. **The audit model reads every segment** of a pair other than en/zh. Until
   now it read only segments a rule had flagged, long ones, and ones with
   numbers, and at most 50 a chapter: 41 of the 163 here. That selection
   was made for English and Chinese, where the rules are strong. The two
   short misplaced lines above were never shown to the model. This costs
   audit time in proportion (about four times on this text).

What this changes in the earlier sections: the "semantic findings" counts of
7.2 and section 6 are counts over the segments the audit model was shown,
not over the whole text. A run with many short lines of dialogue was the
least examined.

Still true after the fixes: the audit model can miss a wrong passage it is
shown. One of the three above (a paragraph of classical quotation replaced by
a paragraph about hulling rice) was read by it and passed.

**Use, after two rounds.** `zh>es`: translategemma (one clean run).
`zh>fr`, `zh>de`: qwen3.8. `zh>ja`: translategemma, with a reader of
Japanese checking every passage against the Chinese; no setup here can be
left to run alone.

**Third round (2026-10-04): `zh>de` resumed, `zh>ja` again as
`bench-zhsrc3-tg-zh-ja`.**

| Pair | Outcome |
|---|---|
| `zh>de` | Finished. No passage out of place by the rules or by comparison with the qwen3.8 run. 73 min (audit 37), 13 of 163 for review; the audit model read 128 segments and reported 58 findings, 14 high. Better German than qwen3.8 ("du Schurke!"), but it shortens ("Ernte, Reismahlen, Bootfahren" for three clauses) and misreads now and then (八月里, "in August", as "in acht Monaten"). |
| `zh>ja` | Reported complete with an empty review queue, in 82 min (audit 47). The all-Chinese passage of round two came out translated. The two passages carrying another passage's text are still there. |

Why those two survived, and the last fix of the round:

6. **A short line of dialogue is not a heading.** The audit sorted segments
   into prose and "structural" (headings, separators), and structural ones
   skip the language checks and the audit model. The heading test looked for
   sentence-ending punctuation at the very end of the line, so a quoted line
   (『你出去！』, “Not I!”) or one introducing speech (他站起来说：) counted as a
   heading: 35 of the 163 segments here, one of the two wrong passages among
   them. The test now looks before the closing quotation mark and treats a
   final colon or ellipsis as prose. On this text 7 segments remain
   structural: the title, the author, and the five chapter headings. This
   holds for en/zh too.

The other wrong passage (classical quotation replaced by a paragraph about
hulling rice) was shown to the audit model in all three rounds and passed
each time. No rule sees it: it stands alone, its length fits, and it is
fluent Japanese.

**Use, after three rounds.**

| Pair | Translator | Rating | Note |
|---|---|---|---|
| `zh>fr` | qwen3.8 | B | Complete; translategemma shortens. |
| `zh>es` | translategemma:27b | B+ | One clean run. |
| `zh>de` | translategemma:27b | B | One clean run; shortens. qwen3.8 (B−) is complete and stiffer. |
| `zh>ja` | translategemma:27b | B, not unattended | A wrong passage passed every check; a reader of Japanese must compare each passage with the Chinese. qwen3.8 is C. |

Not yet run with fix 6: any pair. Its effect on the review queues above is
unknown; it can only add segments to them.

**Fourth round (2026-10-05): `zh>ja` once more, with fix 6
(`bench-zhsrc4-tg-zh-ja`).** 77 min (audit 41); the audit model read 156 of
the 163 segments (128 before, 41 at first) and repair changed 63; 1 segment
for review.

- The short line that carried another passage's text was found and
  translated again, correctly.
- The paragraph of classical quotation is still the paragraph about hulling
  rice: read by the audit model a fourth time and passed.
- Short lines read well; period words stay as kanji compounds (虫豸, 晦気),
  and one line is wrong ("谁认便骂谁" as "誰を罵ってもいい").

One more rule from it:

7. **Almost none of a passage's terms.** A passage with four or more
   approved glossary terms whose translation has at most one of them is
   reported, high, as possibly another passage's translation. A model that
   ignores the glossary still writes the names; a translation of a different
   passage does not. On the 34 saved runs of the last rounds it names that
   paragraph in each of the four Japanese runs, four passages of the first
   German run that were out of place, and nothing else.
8. **Translated again, not edited.** Repair retranslates the whole segment
   for any finding that says the translation is not this passage's or not a
   translation (no target-language text, identical to the source, repeated,
   shifted, another passage's by its terms). Before, it did so only for "no
   Chinese text" and "no English text", by their wording.

Rule 7 needs a glossary with several terms in the passage, so a wrong
passage with fewer than four is still left to the audit model. Neither rule
has been through a run.

**Fifth round (2026-10-05): `zh>ja` with rules 7 and 8
(`bench-zhsrc5-tg-zh-ja`).** 79 min (audit 44); 2 of 163 for review.

- Rule 7 named four passages in the first draft, the paragraph of classical
  quotation among them, and the near-match rule two more. Repair translated
  eight segments again. Five of the six came out right; the three besides
  the known paragraph had in earlier rounds been left to the audit model.
- The known paragraph was translated again twice, and both answers were
  refused: "repair changed an applicable approved term from accepted text".
  The check that a repair keeps the glossary terms of the text it edits
  compared the new translation with the wrong one (阿Q three times there,
  once in the right one). The paragraph went to the review queue, which is
  the first time in five rounds a person would have been shown it.

9. **A retranslation is not held to the wrong text.** When repair
   translates a segment again (rule 8), the glossary and number comparisons
   with the replaced text are skipped, as the quantity comparison already
   was. The audit that follows repair still checks the new text against the
   source.

Rule 9 has not been through a run. With it, the expected result of the same
run is that paragraph translated and one segment left for review.

**A sweep instead of another round (2026-10-05).** Five rounds each found
one more gap. Rather than a sixth, every failed validation in the 45 saved
runs was tallied (`lang_benchmark/results/failures-all.txt`), the code was
searched for
the kinds of gap already met, and the path that failed last was simulated.

What the tally showed, on the repair side:

| Refusal | Times | Runs | Verdict |
|---|---|---|---|
| "repair changed an applicable approved term from accepted text" | 186 | 22 | Too strict outside en/zh (10 below). |
| "repair output is identical to the flagged translation" | 186 | 16 | The model's; the segment goes to review. |
| "repair changed digit-bearing facts from the accepted translation" | 89 | 18 | Partly the wrong-passage case (9); the rest sound, except a two-digit year written out in full ("del 50 al 60" as "1850 to 1860"), which is refused and goes to review. |
| "translation is still written in Simplified Chinese" | 6 | 3 | Right to refuse; the cause is 11. |

10. **A repair may bring a term's count to the source's.** For pairs other
    than en/zh, a repair keeps each term's count between the source's and
    the accepted text's, counting every approved rendering of the term and
    reading the source with its inflection. The en/zh rule (never fewer than
    the accepted text) stays for en/zh.
11. **Feedback repair uses the repair model.** The second repair attempt,
    after review, ran on the primary model whatever `audit.repair_model`
    said, so a Japanese or German text was rewritten by qwen3.8.
12. **The audit schema no longer follows the installed pydantic.** Newer
    releases attach a regular expression to a decimal written as a string;
    it is stripped from the schema sent to the auditor. This was the cause of
    the two failing golden tests: the golden files stand as recorded.
13. **The by-terms finding says both things it can mean**: another passage's
    translation, or most of this one left out (translategemma's French
    reduced a paragraph of quotation to two sentences, and is named by it).

Searched and found sound: no other code matches a finding by its English or
Chinese wording; no other stage picks its segments by an en/zh rule.

Simulated, with the Japanese job's own glossary: a correct retranslation of
the paragraph that failed in round five now passes the repair contract. If it
uses the glossary's renderings of the quotations, the audit after repair
passes it too. If it words them its own way, rule 7 names it again and it
goes to review with the right text: a false alarm in the safe direction,
which is the price of a rule that reads terms and not meaning.

Not provable without a run: what the models write. The rules above are
checked against saved output and by 1,204 tests; whether translategemma's
next draft holds something none of them sees is not knowable from here.

**Runs after the sweep (2026-10-05, `bench-r1-*`, run with
`lang_benchmark/run-benchmarks.ps1`).**

| Run | Time | Review queue | Against |
|---|---|---|---|
| `en>zh` Alice II, VIII, IX, qwen3.8 | 11 min | 1 of 191 | The run before the language work (`bench-alice-3ch-baseline`): 14 min, 0. The same 15 segments read by the audit model, 7 and 8 findings, 7 segments repaired in each, no rule finding in either. No regression. The one segment in review is the audit model's: it reported a quotation left open that the source leaves open too, and repair closed it with a straight mark. |
| `zh>ja` 阿Q正传, translategemma | 74 min | 1 of 163 | Round five: 79 min, 2. All six passages the rules named as another passage's were translated again and are right, the paragraph of classical quotation among them (rule 9 held). Refusals of repairs for the glossary: 4, from 8. |
| `en>ko` Alice 1–4, translategemma | 109 min (audit 84) | 2 of 145 | Round three: 40 min, 4. The audit model read 143 segments, not about 40: 56 findings, 14 high; 48 segments repaired, 21 before. Refusals of repairs for the glossary: none, from 12 (rule 10 held). |

Two things these runs showed, both fixed since:

14. **Feedback repair still ran on the primary model.** Rule 11 had changed
    the stage's label and checkpoint hash, not the call. The call now uses
    the repair model when one is set; a test reads the model from the call.
15. **A translation far from the usual length.** In the Korean run a
    paragraph of nine sentences came back as one sentence, the opening of a
    passage two further on. Nothing named it: it repeats no translation in
    full, the fixed token ratio of the en/zh length check does not fit
    Korean, and the audit model read it and passed it. For pairs other than
    en/zh, a translation under 0.4 or over 2.5 of the usual length for its
    source, the usual being the median of the passages at hand, now fails
    the translation contract and is a high finding in the audit, and repair
    translates it again. On 32 saved runs it names that paragraph, the other
    passages already known to be wrong, and nothing in any qwen3.8 run.

The audit model's misses are the lesson of these rounds. It was shown, and
passed, a paragraph replaced by another, twice in different books. It is a
second reading worth having, and not one to rest on: what the rules can
check (place, length, script, terms, numbers) they must.

The Korean run also puts a number on the cost of reading every segment:
audit 84 minutes of 109, with one segment a call as the benchmark configs
have it (`semantic_max_candidates_per_batch: 1`). The default is 50 a call.

**Confirming run (2026-10-05, `bench-r2-tg-en-ko`).** `en>ko` again with
rules 14 and 15: 92 min (audit 67), 2 of 145 for review.

- The length rule failed two passages at translate time and the shift rule
  four; they were retried or went to the fallback model. The first draft then
  held no passage out of place, of unusual length, or untranslated, and the
  paragraph that came back as one sentence in the run before came back whole.
  The audit raised no finding against a whole translation.
- All ten feedback repairs ran on gemma4:31b.
- The two segments in review are ordinary ones: "half an hour or so" written
  as about an hour, which the number rule found, and a line the repair
  verifier did not settle.
