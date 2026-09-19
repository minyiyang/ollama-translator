# Dashboard localization: plan

Status: **plan, not implemented.** Last updated: 2026-09-18.

How the browser dashboard (`book-agent ui`, source in `frontend/`) becomes
translatable: English and Simplified Chinese first, designed so that more
languages need only a catalog file.

The **interface language** is independent of the **translation direction**. A
user can run the dashboard in any supported interface language while the
pipeline translates `en-zh` or `zh-en`. Adding book languages is a separate,
larger project: prompts, glossary categories (stored in Chinese), and the
deterministic checks all assume Chinese on one side.

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

- **FormatJS (`react-intl`)**, which implements the **ICU message format** on
  top of the browser's built-in `Intl` APIs.
- ICU messages handle plurals for every language, e.g. Russian has 3 forms
  and Arabic 6:

  ```json
  { "jobs.count": "{count, plural, one {# job} other {# jobs}}" }
  ```

- Values are substituted by name (`{job}`, `{done}`), never by concatenation,
  so translators can reorder words.

### 2.2 Catalogs

- `frontend/src/i18n/en.json` is the **source of truth** and must be complete.
- Other languages (`zh-CN.json`, later `ja.json`, `de.json`, …) may be
  **partial**: missing keys fall back to English.
- TypeScript key types are generated from `en.json`, so `t("jobs.addNew")`
  with a misspelled key fails the build.
- English is bundled; other catalogs are **loaded on demand** when selected.

### 2.3 Language selection

- A language menu in the header on every page, listing the available
  catalogs, each named in its own language (English, 中文, 日本語, …).
- Default: the saved choice, otherwise the browser language, otherwise
  English. Saved per browser in `localStorage`.
- Sets `<html lang>` (fonts, line breaking) and `<html dir>` (right-to-left
  languages). Switching is instant: no reload, no lost edits.

### 2.4 Formatting

Numbers, dates, relative times ("2 h ago"), and durations ("12 min") use
`Intl.NumberFormat`, `Intl.DateTimeFormat`, and `Intl.RelativeTimeFormat` for
the selected language, replacing today's hand-written English helpers in
`lib/format.ts`.

### 2.5 Server messages: codes, not sentences

- Messages the user acts on carry a language-neutral **code and values**
  alongside today's English text:

  ```json
  { "error": "a job named a1 already exists", "code": "job_exists", "params": { "job": "a1" } }
  ```

- The UI translates known codes and **falls back to the English text**
  otherwise, so nothing breaks while coverage grows. The CLI stays English.
- Covered: validation problems, blocked actions ("only runs started from this
  dashboard can be stopped"), the glossary lock, draft and start errors, and
  the most common stage status messages ("paused on request; resume to
  continue", "N segment(s) require human review").
- Glossary categories: the server sends the enum name (`PERSON`) and the UI
  looks up the label, giving "Person" in English and 人名 in Chinese.

### 2.6 Layout for any language

- Text can be 30–40% longer (German, Finnish): no fixed widths on labels, and
  buttons and tabs wrap.
- Right-to-left (Arabic, Hebrew):
  - convert CSS to direction-neutral properties (`margin-inline-start`,
    `inset-inline-start`, `border-inline-end` for the sidebar);
  - mirror directional glyphs (‹ ›, the sidebar collapse chevron).

  Today's CSS uses physical left/right in a few dozen places, mostly the
  sidebar, tooltips, and dialogs.
- **Pseudo-locale** for testing: a generated language that stretches and
  accents every string (`[Śţàŕţ ţŕàñšļàţîöñ ~~~]`). Hard-coded strings stay
  plain and overflows become visible, without anyone reading the language.

## 3. Quality checks

| Check | How |
|---|---|
| Key typos | Generated key types; the build fails |
| Missing translations | `npm run i18n:coverage` reports per-language coverage and missing keys |
| New hard-coded text | A test scans `.tsx` files for literal English text in JSX |
| Server codes | A Python test exports every code the server can send; a frontend test checks each has an English entry |
| Formatting | Unit tests for numbers, durations, and relative times in each shipped language |
| Layout | A browser pass in English, Chinese, and the pseudo-locale over every page and state (draft, running, gates, errors) |

## 4. Translation workflow

- Contributors edit JSON catalogs in a pull request, or a translation platform
  (Weblate, Crowdin) syncs the files. Both work with the standard ICU JSON
  format.
- A first draft of a new language may be machine-generated; a native speaker
  reviews it before it ships. The coverage report shows what is left.
- For Chinese, the reference vocabulary is the Chinese README (审校台, 术语表,
  续跑, …) unless the maintainer sets other terms.

## 5. Phases

| Phase | Work | Visible result |
|---|---|---|
| 1 | FormatJS setup, `en.json` with ICU messages, generated key types, language menu; move **all existing strings** into the catalog | None: the English UI is unchanged. A safe refactor covered by existing tests. |
| 2 | `zh-CN.json` for interface text; locale-aware formatting | Switching to 中文 translates the interface |
| 3 | Long help texts: stage descriptions, config help, tooltips | Help and tooltips switch too |
| 4 | Server message codes | Errors and validation results switch |
| 5 | Direction-neutral CSS, pseudo-locale, coverage script, contributor docs | Ready for any further language |
| later | Each additional language: one JSON file plus native review | New entry in the language menu |

Phases 1–4 cost about the same as a two-language-only design. Phase 5 is the
extra cost of supporting any language. Starting with ICU messages avoids
rewriting every message later.

## 6. Decisions needed

1. Ship English and Chinese first on the multi-language design (recommended)?
2. Do phase 5 now, or when a third language is actually requested?
3. Default language: follow the browser, or English until the user switches?
4. Chinese vocabulary: use the Chinese README's terms, or supply a term list?
