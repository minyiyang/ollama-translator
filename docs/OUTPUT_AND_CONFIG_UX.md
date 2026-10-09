# Output formats and config changes in the dashboard

Status: **PR 1 (book output, PDF included) and PR 2 (subtitle output) implemented; PR 3 planned.**
Last updated: 2026-10-09.

Three gaps found while testing the dashboard, each planned as its own pull
request:

| PR | What | Section |
|---|---|---|
| 1 | A book job's output is the format the user wants, PDF included; the EPUB stops being presented as the result. **Implemented** | 2 |
| 2 | A subtitle job's output is a subtitle file in the format the user wants; nothing about it says EPUB. **Implemented** | 3 |
| 3 | A started job's config can be unlocked and changed in the dashboard, with the effect on the pipeline shown and a rerun offered | 4 |

Related documents: docs/FORMAT_SUPPORT.md (sources and exports as built),
docs/STAGE_CONTROL.md (resume and rerun), docs/LOCALIZATION.md (every new
string goes through the catalogs).

## 1. What exists today

**Books**

- The pipeline always builds an EPUB (`compile`) and checks that EPUB
  (`validate_epub`). An RTF job gets an EPUB too: nothing writes RTF.
- A job whose source was text, Markdown, HTML, or a Word document also gets a
  copy in the source's format, written next to the EPUB at compile
  (`book_agent/stages/compile.py`).
- `book-agent export <workspace> --format txt|md|html|docx` and the format
  menu in the job header (`frontend/src/components/JobControls.tsx`) convert
  the compiled EPUB on request. The server converts as the file is asked for
  (`GET /api/jobs/<id>/output?format=`).
- Nothing is written as PDF. A text PDF is a source only
  (docs/FORMAT_SUPPORT.md, 6.1).

**Subtitles**

- A subtitle job writes a file of the kind it was given (`.srt` in, `.srt`
  out): the source's cues and times with the translated text
  (`compile_subtitle_file` in `book_agent/subtitles.py`). It never writes an
  EPUB, and `book-agent export` refuses a subtitle job.
- Nothing converts one subtitle format to another.
- The stages are shared with books, so the interface says "Compile EPUB",
  "Validate EPUB", and `EPUB: <path>` on a subtitle job too.

**Config**

- A started job's config is a snapshot captured when the job was created. The
  Config tab shows it read-only (`frontend/src/pages/ConfigPage.tsx`).
- `book-agent status` reports whether the config file has drifted from the
  snapshot, and `book-agent config-diff <workspace> --config <file>` lists the
  differences. Both only report.
- A stage can be rerun from the Progress tab, with a preview of what is redone
  and what is lost (docs/STAGE_CONTROL.md).
- Each stage records a hash of its inputs, the config values it reads among
  them. A stage whose hash no longer matches is redone, with every stage after
  it, the next time the job runs.

The gaps:

1. The EPUB is presented as the result. The Jobs list downloads only the
   EPUB, the default is always EPUB, and a PDF cannot be had at all.
2. A subtitle job cannot be given back in another subtitle format, and its
   interface text talks about a book.
3. Changing a started job's config means editing files by hand and knowing
   which stage to rerun.

## 2. PR 1: book output formats

### 2.1 Decisions

- **The EPUB is still built**, as the form every other format is made from
  and the thing the completeness checks run on. It is an internal step, not
  the result.
- **A job has an output format.** New config key `output.format`, default
  `source`: the book comes back in the format it came in.

  | Source | `output.format: source` gives |
  |---|---|
  | EPUB | EPUB |
  | Text, Markdown, HTML, Word | the same format (as today's extra copy) |
  | PDF | PDF (new; EPUB until the PDF writer exists) |
  | RTF | EPUB (as today: nothing writes RTF, and no RTF writer is added) |

  Any of `epub`, `txt`, `md`, `html`, `docx`, `pdf` can be set instead.
- **Both of the options discussed are kept.** The setting decides what the
  job produces and what **Download** gives by default; the format menu stays
  beside it, so another format is one click away with no recompile.
- **Text PDF is added as an output** (2.3).

### 2.2 Changes

| Where | Change |
|---|---|
| `book_agent/config.py` | `OutputConfig` with `format`; validated against the job's kind when the job starts |
| `stages/compile.py` | Writes the configured format next to the EPUB, in place of "the source's format if it was converted". The format is part of the stage's input hash, so changing it redoes only compile and validation |
| `stages/compile.py`, `web/server.py` | "The job's output" (`load_compiled_epub_path`, `job_output`) becomes the file in the configured format; the EPUB stays reachable as `?format=epub` |
| `cli.py` | `export --format` accepts `pdf` and `epub`; `run` prints the output path in the configured format |
| Jobs list | **Download** gives the configured format, not the EPUB |
| Job header | The format menu starts on the configured format and lists PDF |
| Config editor | An "Output" group with the format, with help text on what each format keeps |
| Final review, Text tab | The compile card and the recompile dialog name the output file, not "EPUB" |
| Stage names | "Compile EPUB" → "Build the book"; "Validate EPUB" → "Check the book". The stage IDs (`compile`, `validate_epub`) do not change: they are in every job's state and logs |

All seven catalogs get the new and changed strings (`npm run i18n -- status`).

### 2.3 Writing a PDF

The PDF is the book's text reflowed onto pages, like the other exports: the
headings, paragraphs, emphasis, pictures, and notes that `export_book` already
carries. It is not the source PDF's layout, which the reader does not keep
(docs/FORMAT_SUPPORT.md, 6.1).

It needs a library; the project has none that writes PDF.

**Decided (2026-10-09): `reportlab`**, as an optional extra
(`pip install book-agent[pdf]`). Asking for a PDF without it installed is
refused with that command in the message, before any model call is made.

| Option | Licence | Why, or why not |
|---|---|---|
| **`reportlab`** | BSD | Chosen. Embeds TrueType fonts, lays out paragraphs, and has a line-breaking mode for text without spaces (Chinese, Japanese). Keeps every dependency permissive beside the project's MIT licence |
| `fpdf2` | LGPL-3.0 | Smaller, and usable from an MIT project as a dependency, but the one copyleft library the project would have |
| `xhtml2pdf` | Apache-2.0 | HTML to PDF on top of `reportlab`; weaker with CJK than using `reportlab` directly |
| WeasyPrint | BSD | The best output from HTML, but needs native Pango libraries, awkward to install on Windows |
| PyMuPDF, borb | AGPL | Ruled out by the licence |
| Headless Chromium | — | Prints the HTML export well, but a command-line tool would need a browser installed |
| Written by hand | — | Workable for Latin text in the built-in fonts only. Chinese, Japanese, and Korean need an embedded, subsetted font with its character maps and glyph widths, which is most of what a PDF library is |

The export is built from the same book model the other formats use
(`read_epub` in `export_book`): headings, paragraphs, emphasis, pictures, and
notes become `reportlab` flowables. It does not go through the HTML export.

**Fonts.** The font is embedded, so the file reads the same on every machine.
`reportlab`'s built-in CJK font names are not used: they are not embedded, and
the reader's machine must then have them.

- A font must cover the language translated into. No single small font covers
  Latin, Chinese, Japanese, and Korean, and bundling one for each adds tens of
  megabytes, so none is bundled.
- `output.pdf_font` is a path to a font file. When unset, a known system font
  for the target script is looked for (for example Noto, Microsoft YaHei,
  PingFang). When none is found, the PDF is refused with a message that names
  the setting.
- `reportlab` embeds fonts with TrueType outlines (`.ttf`, and `.ttc` by
  index), not the CFF-outline `.otf` files Noto CJK is often shipped as. The
  system-font list must hold only fonts it can embed, and a font it cannot is
  refused by name rather than failing inside the library.
- Right-to-left text and complex shaping are out of scope for the first
  version; a job translating into such a language is refused PDF with that
  reason.

**Trial:** run on Windows (2.4); the writer was small enough to stay in PR 1.

### 2.4 As built

Where the code is: `book_agent/output.py` (which format a job gives back),
`book_agent/pdf_export.py` (the PDF writer and its fonts), the compile stage
(`compiled_output` in the job's state names the file given back), and
`GET /api/jobs/<id>/output` (that file, or `?format=` for another).

What differs from the plan above, or was settled while building:

- **The font trial** ran on Windows only. reportlab embedded and subsetted
  every font tried (Microsoft YaHei, SimSun, SimHei, Noto Sans SC, Yu Gothic,
  MS Gothic, Malgun Gothic, Arial, Times New Roman, Segoe UI), and broke
  Chinese and Japanese lines without spaces. macOS and Linux are untried: the
  font names looked for there are in `pdf_export.py` and may need adding to.
- **A font is chosen by the book's letters.** The fonts looked for are tried
  in order, and the first that has the book's letters is used. A font that
  cannot be embedded is passed over, not failed on.
- **Latin always has a font.** reportlab brings one (Bitstream Vera, Western
  European letters); it is the last one tried. The tests use it, so they need
  no font of the machine they run on.
- **A PDF nobody named is not insisted on.** A book that came as a PDF, with
  `output.format: source`, comes back as an EPUB where a PDF cannot be
  written, and `run` says so. `output.format: pdf` that cannot be written is
  refused when the job starts.
- **Scripts that join or run right to left are refused by the language's
  script** (Arabic, Hebrew, Devanagari, Thai, and the others reportlab does
  not shape), before the job starts.
- **The page** is A5 with page numbers; a chapter (a first-level heading)
  starts a page. Notes are set where the book has them, each led by its
  number; a reference to one is a raised number, not a link. Bold and italic
  use the font's own files where the system has them, and the regular
  otherwise (a Chinese font has no italic).
- **A job that names no format is compiled as it was.** The output format is
  part of the compile stage's hash only when it differs from what the job
  gave back before this change, and the config is written without an
  `output` section when it has only defaults, so existing jobs are neither
  compiled again nor reported as drifted.
- **The dashboard is told what can be written.** A job's info carries
  `output_format` and `output_formats`; the menu leaves PDF out where the
  server cannot write one.
- **The typography is plain.** A comma or a small kana can begin a line in
  Chinese and Japanese; reportlab's line breaking does not keep all of them
  off the line start.
- **Translations.** The 15 new or reworded strings are in all six translated
  catalogs, and the opening of each translated README names the output
  formats. The PDF details (reportlab, fonts, refused scripts) are in the
  English README only, as the other format details are.

### 2.5 Tests

- Unit: `output.format` resolves `source` for each kind of source; an
  unsupported pair (a subtitle format on a book) is refused at validation.
- Stage: compile writes the configured format; changing the format redoes
  compile and validation only.
- PDF: a written PDF read back with the project's own PDF reader gives the
  same headings and paragraphs; a missing font is refused with the message.
- Browser: the Jobs list download and the header menu give the configured
  format (extend `frontend/e2e/formats.e2e.ts`).

## 3. PR 2: subtitle output formats

### 3.1 Decisions

- **A subtitle job's output is a subtitle file**: SRT, WebVTT, or ASS.
  `output.format` takes `source` (default), `srt`, `vtt`, or `ass` for a
  subtitle job. ("webt" in the request is read as WebVTT, `.vtt`.)
- **EPUB is removed from a subtitle job**: no EPUB in its format menu, its
  stage names, its compile card, or its messages. The server refuses
  `?format=epub` and every book format for a subtitle job.
- **The same flow as a book**: the setting gives the default, and a format
  menu in the job header and on the Jobs list gives the others on request.

### 3.2 Converting

Times and cue order are never changed. What differs between the formats:

| From → to | Kept | Lost or added |
|---|---|---|
| SRT ↔ WebVTT | Cues, times, line breaks, `<i>` `<b>` `<u>` | WebVTT's cue settings (position, alignment), `NOTE` and `STYLE` blocks are dropped going to SRT; a `WEBVTT` header is added going the other way |
| SRT or WebVTT → ASS | Cues, times, line breaks, italics and bold as ASS tags | A default style block is added: one style, bottom centre |
| ASS → SRT or WebVTT | Dialogue text, times, italics and bold | Styles, positioning, colours, karaoke timing, and drawing commands. Events that overlap in time (a sign on screen during a line) become separate cues |

A conversion that loses something is allowed and says what it
lost, in the download dialog and in the CLI's output. It is not refused: a
plain SRT from a styled ASS file is a common thing to want.

The same-format path stays as it is today: the source file is rewritten in
place, so everything the pipeline did not translate is byte-for-byte the
source's. Only a conversion goes through the new writer.

### 3.3 Changes

| Where | Change |
|---|---|
| `book_agent/subtitles.py` | `convert_subtitles(parsed, translations, limits, kind)`: a writer for each format from the parsed cues, and a list of what was lost |
| `stages/compile.py` | Writes the configured subtitle format |
| `stages/validate_epub.py` | The check that every cue is present with its time runs on the converted file too |
| `cli.py` | `export --format srt|vtt|ass` for a subtitle job, in place of today's refusal |
| `web/server.py` | `?format=` accepts the three subtitle formats for a subtitle job |
| Job header, Jobs list | A format menu for subtitle jobs, as for books |
| Stage names and help | On a subtitle job: "Write the subtitle file" and "Check the subtitle file", with their own descriptions. The job's kind is already known to the page (`job_type`) |

### 3.4 Tests

- Unit: each of the six conversions on a fixture with italics, a two-speaker
  cue, and a cue with positioning; times identical before and after; the
  "lost" list is what the table above says.
- Round trip: SRT → WebVTT → SRT gives the first file's cues and text.
- Stage: `validate_compiled_subtitles` passes on every converted output.
- Browser: a subtitle job offers the three formats and no book format; the
  page has no "EPUB" on it (extend `frontend/e2e/subtitles.e2e.ts`).

### 3.5 As built

- **The file of the kind that came in is always written**, as before, and is
  what the stage checks first. Another format is made from that file, not
  from the pipeline's documents: `convert_subtitles(parsed, kind)` in
  `book_agent/subtitles.py` reads the compiled file and writes it again.
  This is the same shape as a book's EPUB and its other formats, and it means
  a download in another format needs nothing but the compiled file.
- **What a conversion keeps.** A cue's lines, its times, and the italics,
  bold, and underline that stood around the whole cue. Markup inside a cue
  was already dropped when the cue was translated. A position code SubRip
  borrowed from ASS (`{\an8}`) is kept going to ASS and dropped going to
  WebVTT.
- **Text a format would read as markup.** SubRip and ASS cannot say that a
  `<`, a `{`, or in ASS the backslash of `\N`, `\n`, `\h` is text
  (`<door closes>` from a WebVTT `&lt;door closes&gt;`, a path such as
  `C:\new\home`). The writer puts a word joiner (U+2060, which is not seen)
  after such a character, so that neither a player nor this reader takes
  what follows for a tag or a code, and the reader takes it out again
  (`_literal`, `_GUARD`). WebVTT has entities and uses those. The same is
  done when a translation is written into the file of the kind that came
  in.
- **What it says it lost** is a list of codes (`CONVERSION_NOTES`), found by
  looking at the file, not assumed from the pair of formats: a plain SRT
  written as WebVTT loses nothing and says nothing. The codes are
  `cue_settings`, `blocks`, `markup`, `position`, `styles`, `drawings`,
  `times`, `default_style`. The CLI prints them on stderr; the job's info
  carries them as `output_notes`, and the dashboard shows them as the file is
  downloaded.
- **Times in ASS** are hundredths of a second, so a time is rounded by at
  most 5 ms. That is the one case where a time is not the source's, and it is
  the `times` note. The check allows exactly that much.
- **Drawing events** of an ASS file (`\p1`) are not lines anyone reads and
  are left out of SRT and WebVTT; the check expects them gone.
- **An `.ssa` file** stays `.ssa` under `source`, and under `ass`, which is
  its kind already. It is offered as SSA, SRT, and WebVTT.
- **The check.** `validate_converted_subtitles` reads the converted file back
  against the compiled one: the same number of cues, in order, at the same
  times, each with the same text. The validate stage runs it whenever the
  job's output is not the compiled file itself.
- **Refusals.** `output.format` is one setting for both kinds of job. A
  subtitle job with a book's format, or a book with a subtitle format, is
  refused before any model call (`check_output`), by `export`, and by
  `GET /api/jobs/<id>/output?format=`.
- **Said when the config is validated.** The dashboard's Validate step runs
  the same check (`validate_setup`), for books and subtitle jobs: a format the
  job cannot be given, or a PDF that cannot be written, is a problem listed
  there instead of a job that fails as it starts.
- **Existing jobs.** A subtitle job that names no format, or its own, hashes
  as it did and is not compiled again.
- **Wording.** A message with a wording of its own for a subtitle job has the
  same key ending in `.subtitles`; `jobKey(key, jobType)` picks it
  (`frontend/src/i18n/index.ts`). The stage names and descriptions, the
  compile card, the recompile dialog, and the format menu's help have one.
  Two server messages that named a book were reworded for both kinds.
- **Settings by kind of job.** The Config tab's form leaves out what the
  other kind alone reads (`ONLY_FOR` in `frontend/src/lib/configCatalog.ts`):
  for a subtitle job `epub.*`, `reprose.*`, `output.pdf_font`,
  `translation.translated_title`, and `consistency.quoted_speech`
  (subtitles are dialogue without quotation marks, so that check finds
  nothing in them); for a book `subtitles.*`. The settings a subtitle job
  does see are worded for it where the book's wording speaks of a book or
  its chapters ("Extract terms from the subtitles", "Earlier parts in
  context"), and the group once called "Book consistency" is "Content
  consistency" for both kinds. `output.format`
  offers the formats of the job's kind. The YAML tab shows the whole file,
  and a line above the form says what is left out.
- **The Jobs list has the menu too**, for books and subtitle jobs alike: one
  component (`frontend/src/components/DownloadControl.tsx`) is the job
  header's and the list's. The list's rows carry `output_format`,
  `output_formats`, and `output_notes` for finished jobs.
- **The title stage is not listed for a subtitle job.** A subtitle file has
  no title, contents, notes, or pictures, so `translate_title` has nothing to
  do. It still runs and passes at once (the pipeline is one for both kinds);
  the server leaves it out of what the pages list and count
  (`shown_stages` in `book_agent/web/jobs.py`): the Progress table, the
  stage counts, the rerun preview, the estimate, and the pipeline shown when
  a config is validated. `book-agent status` still lists it.

## 4. PR 3: changing a started job's config

### 4.1 Decisions

- **Unlock on the Config tab.** A started job's config stays read-only until
  **Unlock to edit** is pressed. Unlocking is offered when the job is paused,
  stopped, failed, or complete; not while it runs.
- **The change is shown before it is saved**: which settings changed, and for
  each the first stage it affects.
- **Two ways to save:**
  - **Save and rerun from *stage*** — the earliest stage any change affects.
    It opens the rerun dialog that exists today, with its list of what is
    redone and what is lost (docs/STAGE_CONTROL.md, 2).
  - **Save only** — offered when no finished stage is affected: the change
    applies to the stages still to run.
- **Some settings stay locked** after a job starts, because changing them
  makes it a different job: the translation direction, and the paths (`paths`).
  They are shown with the reason.
- Saving writes the job's snapshot and records the change (old value, new
  value, when) in the job's log, so a result can be traced to the config that
  produced it.

### 4.2 How the effect of a change is known

Each setting is mapped to the first stage that reads it:

| Setting (examples) | First stage affected | What that means |
|---|---|---|
| `glossary.extraction_*` | Extract glossary | The glossary is extracted again; its approval and the whole translation are redone |
| `translation.style`, `translation.model`, `ollama.model` | Translate | The book is translated again |
| `audit.*` | Audit translation | The translation is kept; audit, repair, and review are redone |
| `reprose.*` | Rewrite prose | The repaired text is kept; rewrite and review are redone |
| `workflow.compile_max_unresolved_review_segments`, `epub.*`, `output.*`, `subtitles.*` | Compile | Only the output is rebuilt: seconds, no model call |
| `workflow.max_retries`, `ollama.timeout_seconds` | none | Applies to whatever runs next |

- The map lives in one place on the server (`book_agent/web/config_impact.py`)
  and is served with the config, so the dashboard and
  `book-agent config-diff` say the same thing.
- **It must not drift from the pipeline.** Two tests keep it honest: every
  leaf of `AppConfig` has an entry, so a new setting cannot be added without
  one; and for a sample of settings, changing the value in a fixture job makes
  exactly the mapped stage's input hash stale.
- The sentence under each change ("The book is translated again") is a
  catalog string per stage, not per setting, so seven languages need about
  twenty new sentences rather than one per setting.

The table above is illustrative. The real one is written from each stage's
hashed inputs when the PR is built, not from memory.

### 4.3 Changes

| Where | Change |
|---|---|
| `web/config_impact.py` (new) | The map, and `impact(old, new)`: the changed settings, each with its stage, and the earliest stage overall |
| `web/server.py` | `POST /api/jobs/<id>/config/preview` (the impact of a proposed text) and `POST /api/jobs/<id>/config` (validate, save the snapshot, log the change); both refused while the job runs |
| `workflow.py` | A function that replaces the snapshot and its recorded hash together |
| `cli.py` | `config-diff` prints the affected stage for each change; `config-apply <workspace> --config <file>` does from the terminal what the dashboard does |
| Config tab | **Unlock to edit**, the locked settings with their reason, the change list with its effects, the two save buttons |
| Progress tab | After a save with no rerun, a banner on the stages now out of date, with **Rerun from here** |

### 4.4 Tests

- Unit: `impact` for one setting per stage, for several at once (the earliest
  wins), and for a locked setting (refused).
- The two drift tests in 4.2.
- Server: preview and save are refused while the job runs; a save changes the
  snapshot and the recorded hash; an invalid config is refused with the same
  messages the draft editor gives.
- Component: unlock, edit, see the change list, both save paths.
- Browser: change the unresolved-segment limit on a paused job, save and
  rerun from compile, and the job finishes.

## 5. Order and size

1. **PR 1, book output.** The setting, the default, the wording, and the
   Jobs list download are small. The PDF writer is the uncertain part and
   could be split off as PR 1b if the trial in 2.3 shows it is large.
2. **PR 2, subtitle output.** Depends on PR 1's `output.format` and format
   menu.
3. **PR 3, config changes.** Independent of the other two, and the largest.

## 6. Decisions

All settled on 2026-10-09.

1. **PDF library:** `reportlab` (2.3).
2. **PDF fonts:** a font found on the system or named in `output.pdf_font`,
   embedded, none bundled. Tried on Windows only (2.4).
3. **A PDF source's default output** (2.1): PDF once the writer exists. Until
   then it stays EPUB.
4. **RTF** (2.1): an RTF job keeps getting an EPUB; no RTF writer is added.
   PR 1 corrects the interface text that says "the EPUB (or RTF)".
5. **Lossy subtitle conversion** (3.2): allowed, with a notice of what was
   lost.
6. **Config edits while a job runs** (4.1): not offered. The job is paused
   first.
