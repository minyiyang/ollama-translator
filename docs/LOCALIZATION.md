# Dashboard localization

Status: **phases 1 to 4 implemented; 5 deferred.**
Last updated: 2026-10-08.

How the browser dashboard (`book-agent ui`, source in `frontend/`) is
translated. It ships in the seven languages the pipeline has a profile for
(`book_agent/languages.py`): English, Simplified Chinese, Japanese, French,
Spanish, German, and Korean. A further language needs only a catalog file.

The **interface language** is independent of the **translation direction**. A
user can run the dashboard in any interface language while the pipeline
translates any pair (docs/GENERIC_LANGUAGES.md).

## 1. Scope

| Translate | Keep as-is |
|---|---|
| Interface text: tabs, buttons, headings, banners, toasts, empty states | Book content: source, translations, glossary terms, notes, evidence |
| Help and tooltips: stage descriptions (`lib/stageInfo.ts`), config labels and help (`lib/configCatalog.ts`), Keep/Defer/Drop explanations | Config keys and paths (`audit.model`, `runs\…`), model names, job IDs |
| Category labels, glossary flags, stats labels | Session log lines: diagnostics; translating them would break searching and bug reports |
| Server messages the user acts on: validation problems, blocked actions, error banners | Raw exception text: shown as-is under a translated heading |
| Dates, numbers, durations, relative times | |

## 2. Design

### 2.1 Library and message format

- Messages are in the **ICU message format**, formatted by FormatJS's
  `intl-messageformat` on top of the browser's built-in `Intl` APIs.
- `frontend/src/i18n/index.ts` is the whole runtime: `useT()` in a component,
  `t()` in plain code, and `rich()` for a message that wraps an element:

  ```tsx
  const t = useT();
  t("shell.checksSkipped", { direction, count });
  rich("glossary.notReady", { link: (chunks) => <Link to={progress}>{chunks}</Link> });
  ```

  `react-intl` was not needed: the hook re-renders a component when the
  language changes, and code outside a component can translate too.
- ICU messages handle plurals for every language, e.g. Russian has 3 forms
  and Arabic 6:

  ```json
  { "jobs.count": "{count, plural, one {# job} other {# jobs}}" }
  ```

- Values are substituted by name (`{job}`, `{done}`), never by concatenation,
  so translators can reorder words.

### 2.2 Catalogs

- `frontend/src/i18n/en.json` is the **source of truth** and must be complete.
- Other languages are in `frontend/src/i18n/locales/` (`zh-CN.json`,
  `ja.json`, …) and may be **partial**: missing keys fall back to English.
- Keys are typed from `en.json`, so `t("jobs.addNew")` with a misspelled key
  fails the build.
- English is bundled; other catalogs are **loaded on demand** when selected.

### 2.3 Language selection

- A language menu at the right of the header on every page, listing the
  available catalogs, each named in its own language (English, 简体中文,
  日本語, …).
- Default: the saved choice, otherwise **English**. The browser's language is
  not consulted. Saved per browser in `localStorage` (`ui-language`).
- Sets `<html lang>` (fonts, line breaking). Switching is instant: no reload,
  no lost edits.

### 2.4 Formatting

- Counts use `toLocaleString` and dates `toLocaleDateString` for the selected
  language.
- Durations and relative times ("12 min", "2 h ago") are catalog messages
  (`format.*`), so each language writes its own compact form and the English
  output is what it was before.

### 2.5 Server messages: codes, not sentences (phase 4)

- Messages the user acts on carry a language-neutral **code and values**
  alongside today's English text:

  ```json
  { "error": "a job named a1 already exists", "code": "job_exists", "params": { "job": "a1" } }
  ```

- The UI translates known codes and **falls back to the English text**
  otherwise, so nothing breaks while coverage grows. The CLI stays English.
- The codes and their English text are in `book_agent/web/messages.py`; the
  server raises `UserError(code, **values)`. The catalog holds each under
  `server.error.<code>`, and a rerun's warnings under `server.rerun.<code>`.
- Stage status messages ("paused on request; resume to continue", "N
  segment(s) require human review") are written by the pipeline, which the CLI
  shares, so they stay English in the workspace. The UI recognizes them by
  their English wording (`server.stage.<code>`, `lib/serverText.ts`).
- Status and enum words (stage status, finding category, glossary category,
  review mode, …) are looked up by value in `lib/enums.ts`; a value the
  catalog does not know is shown as the server sent it.
- Still English: errors without a code, compile log lines, the reasons an LLM
  reviewer gives, and the names of skipped checks.

### 2.6 Layout for any language (phase 5)

- Text can be 30–40% longer (German, Finnish): no fixed widths on labels, and
  buttons and tabs wrap.
- Right-to-left (Arabic, Hebrew):
  - set `<html dir>`;
  - convert CSS to direction-neutral properties (`margin-inline-start`,
    `inset-inline-start`, `border-inline-end` for the sidebar);
  - mirror directional glyphs (‹ ›, the sidebar collapse chevron).

  Today's CSS uses physical left/right in a few dozen places, mostly the
  sidebar, tooltips, and dialogs. None of the seven shipped languages is
  right-to-left.
- **Pseudo-locale** for testing: a generated language that stretches and
  accents every string (`[Śţàŕţ ţŕàñšļàţîöñ ~~~]`). Hard-coded strings stay
  plain and overflows become visible, without anyone reading the language.

## 3. Quality checks

| Check | How | Status |
|---|---|---|
| Key typos | Keys are typed from `en.json`; the build fails | done |
| New hard-coded text | `i18n/hardcoded.test.ts` parses every component and fails on literal text in JSX or in a `title`, `aria-label`, or `placeholder` | done |
| Broken translations | `i18n/catalog.test.ts`: every message parses, has no key English lacks, and uses the same values as the English message | done |
| Missing translations | `npm run i18n -- status` reports per-language coverage | done |
| Server codes | `tests/test_web_messages.py`: the English catalog has every code the server sends, with the server's text, and no other | done |
| Layout | A browser pass in each language and the pseudo-locale over every page and state (draft, running, gates, errors) | phase 5 |

## 4. Translation workflow

- Contributors edit JSON catalogs in a pull request, or a translation platform
  (Weblate, Crowdin) syncs the files. Both work with the standard ICU JSON
  format.
- For a batch of new messages, in `frontend/`:
  - `npm run i18n -- md` writes `i18n-work/strings.md`, one numbered section
    per message some language lacks;
  - translate the text under each heading, keeping the numbers, the
    `{values}`, and config keys such as `audit.model`;
  - `npm run i18n -- merge-md <locale> <file>` checks the file and merges it.
    A message that is empty, is not valid ICU, or takes other values than the
    English one stops the merge.
- The first catalogs for Japanese, French, Spanish, German, and Korean were
  machine-drafted and **have not been reviewed by a native speaker**.
- Vocabulary: each language's catalog uses the terms of that language's
  README (`README.zh-CN.md`: 审校台, 术语表, 续跑, …; `README.ja.md`,
  `README.fr.md`, `README.es.md`, `README.de.md`, `README.ko.md`). Change a
  term in both places.

## 5. Phases

| Phase | Work | Status |
|---|---|---|
| 1 | Message runtime, `en.json` with ICU messages, typed keys, language menu; all interface strings moved into the catalog | done |
| 2 | Catalogs for the six other languages; locale-aware formatting | done |
| 3 | Long help texts: stage descriptions (`lib/stageInfo.ts`), config labels and help (`lib/configCatalog.ts`) | done |
| 4 | Server message codes; status and enum words | done |
| 5 | Direction-neutral CSS, pseudo-locale, contributor docs | deferred until a right-to-left or eighth language is asked for |
| later | Each additional language: one JSON file plus native review | |

## 6. Decisions (2026-10-08)

1. Ship the seven languages the pipeline has profiles for, on the
   multi-language (ICU) design.
2. Phase 5 is deferred.
3. The default language is English, not the browser's.
4. Each language's vocabulary follows that language's README.
