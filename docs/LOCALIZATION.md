# Dashboard localization

Status: **phases 1 to 5 implemented.**
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
- A message worded differently for a subtitle job has a second key, the same
  one ending in `.subtitles` (`stage.compile.does.subtitles`).
  `jobKey(key, jobType)` picks it for a subtitle job and the plain key
  otherwise, so a message without one is shared by both kinds of job.

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

- Text can be 30–40% longer (German, Finnish): no fixed widths on labels,
  buttons and tabs wrap, and a chip that cannot fit is cut with an ellipsis.
- Right-to-left (Arabic, Persian, Hebrew, Urdu):
  - `<html dir>` follows the interface language (`direction()` in
    `i18n/index.ts`);
  - the CSS uses direction-neutral properties (`margin-inline-start`,
    `inset-inline-start`, `border-inline-end`, `text-align: start`), so the
    header, sidebar, tables, and tooltips mirror without further rules. Write
    new CSS the same way; physical `left`/`right` is kept only where the thing
    is left to right in every language (the code editor);
  - the few rules that logical properties cannot express (the switch knob,
    the collapsed-group caret) are under `[dir="rtl"]` at the end of
    `pages.css`;
  - « ‹ › » mirror by themselves in right-to-left text. Arrows (→) do not: a
    right-to-left catalog writes ←.
- What does not follow the interface language:
  - code, config keys, IDs, and logs (`.mono`, `code`, `pre`, `.log`) are
    always left to right;
  - book text is laid out in the direction of its own language. Give an
    element that shows source or translated text its `lang`; the CSS does the
    rest, so an Arabic translation reads right to left in an English
    interface, and an English source left to right in an Arabic one.
- **Layout-test languages** (`i18n/pseudo.ts`), generated from the English
  messages, so they need no catalog:
  - `en-XA` accents every letter and makes each message a third longer
    (`šţàŕţ ţŕàñšļàţîöñ ~~~~~~`);
  - `ar-XB` writes the messages in Arabic letters, so the page runs right to
    left, and turns the arrows.

  Text that stays plain was never in the catalog, and overflows show without
  anyone reading the language. `npm run dev` lists both in the language menu.
  In a built dashboard, run
  `localStorage.setItem("ui-language", "ar-XB")` in the browser console and
  reload; pick a language from the menu to leave. No right-to-left language is
  shipped yet.

## 3. Quality checks

| Check | How | Status |
|---|---|---|
| Key typos | Keys are typed from `en.json`; the build fails | done |
| New hard-coded text | `i18n/hardcoded.test.ts` parses every component and fails on literal text in JSX or in a `title`, `aria-label`, or `placeholder` | done |
| Broken translations | `i18n/catalog.test.ts`: every message parses, has no key English lacks, and uses the same values as the English message | done |
| Missing translations | `npm run i18n -- status` reports per-language coverage | done |
| Server codes | `tests/test_web_messages.py`: the English catalog has every code the server sends, with the server's text, and no other | done |
| Layout | `e2e/layout.e2e.ts`: in both layout-test languages no page is wider than the window; right to left, the header, sidebar, and tables start from the right while identifiers and book text keep their own direction | done |

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
| 5 | Direction-neutral CSS, right-to-left layout, layout-test languages | done |
| later | Each additional language: one JSON file plus native review | |

### Adding a language

1. Add it to `LOCALES` in `frontend/src/i18n/index.ts`, named in its own
   language. A right-to-left language also goes in `RIGHT_TO_LEFT` there if
   it is not already listed.
2. Create `frontend/src/i18n/locales/<code>.json` as `{}` and fill it with
   `npm run i18n -- md` and `merge-md` (section 4). Missing messages show in
   English meanwhile.
3. Run `npm test` and `npm run e2e`, then `npm run build`.
4. Per decision 4, add `README.<code>.md` and take the interface vocabulary
   from it.

## 6. Decisions (2026-10-08)

1. Ship the seven languages the pipeline has profiles for, on the
   multi-language (ICU) design.
2. Phase 5 was deferred, then built the same day as layout only: no
   right-to-left language is shipped, and Arabic is not in the menu.
3. The default language is English, not the browser's.
4. Each language's vocabulary follows that language's README.
