# More book formats

Status: **implemented** for text, Markdown, HTML, and Word (.docx), as
sources and as extra outputs (`book_agent/book_formats.py`). This document
records what similar projects support, what was decided, how it is built,
what it was tried on, and what stands in the way of subtitles and game text.

## 1. What similar projects support

| Project | Reads | Writes |
|---|---|---|
| bilingual_book_maker | EPUB, TXT, Markdown, SRT, PDF | The input's format, bilingual; a PDF also gets an EPUB |
| TranslateBooksWithLLMs | EPUB, SRT, DOCX, PDF, TXT | The input's format. A PDF is reflowed, not laid out as the original; no OCR |
| Tolmach (KazKozDev/book-translator) | TXT, EPUB, PDF, DOCX | TXT, PDF, EPUB |
| booktrans | EPUB, FB2, HTML, PDF, Markdown, TXT | EPUB, FB2, HTML, Markdown, TXT, LaTeX, PDF |
| ebook-GPT-translator | TXT, EPUB, DOCX, PDF, optional MOBI | not checked |
| AiNiee | EPUB, TXT, Word, PDF, Markdown, PowerPoint, four subtitle formats, game text | not checked |
| Ebook Translator (Calibre plugin) | Whatever Calibre reads (48 formats), and SRT | Whatever Calibre writes (20 formats) |
| epub-translator (oomol) | EPUB only; sends scanned PDFs through a separate converter first | Bilingual EPUB |
| MoxHome | EPUB | EPUB, PDF, DOCX, FB2, AZW3, TXT, Markdown, HTMLZ |

The first two, AiNiee, the Calibre plugin, and epub-translator were read from
their READMEs in October 2026; the other rows come from search summaries and
are less certain.

Two things stand out. The common set beyond EPUB is TXT, DOCX, and PDF. And
PDF is weak everywhere: the one project that documents its PDF handling
reflows the text, drops the page layout, and has no OCR.

## 2. Decision

| Format | Decision |
|---|---|
| TXT, Markdown, HTML, DOCX as sources | **Add** |
| The translated book as TXT, Markdown, HTML, DOCX | **Add**, beside the EPUB |
| FB2, MOBI, AZW3 | Skip |
| Scanned PDF | Skip: OCR is a different problem |
| Text PDF | Not decided. Paragraph and heading reconstruction is unreliable, and the audit works passage by passage |
| Subtitles, game text | Not books; section 7 lists what they would need |

## 3. Sources: design

RTF shows the way: it is turned into the pipeline's own document inventory at
the decompile stage. These formats go one step further and become a real
EPUB package there, so every later stage, the compile included, treats the
book as it treats any EPUB.

1. **One shape for all four.** A reader per format returns a list of blocks:
   heading (levels 1 to 6), paragraph, bullet, numbered item, quotation,
   preformatted text, rule. A block's text is inline XHTML: escaped text with
   `em`, `strong`, `code`, `a href`, `sub`, `sup`, `br`.
2. **Readers.**
   - *Text.* Encoding from a byte-order mark, then UTF-8, GB18030, CP1252. A
     file wrapped at a fixed width (several short lines to a paragraph, blank
     lines between) has its lines joined, without a space between two
     characters of a script written without spaces; otherwise every line is a
     paragraph, as novels saved from the web are. A short line that reads as
     a chapter heading ("Chapter 3", "第一章", "제1장") is one.
   - *Markdown.* A subset, written here: headings of both kinds, emphasis,
     links, inline code, lists, quotations, fenced code, rules, front matter
     for title and author. A picture is replaced by its description.
   - *HTML.* BeautifulSoup, already a dependency. Scripts, styles, and
     navigation are dropped; a link is kept only if it is http, https,
     mailto, or within the page; a table's cells become paragraphs.
   - *Word.* The document's XML read directly, with no new dependency.
     Headings are found by the style's name, not its id, since the id is
     whatever the author's copy of Word made it. Runs of the same emphasis
     are joined. A list is a bullet or a numbered list by its numbering
     definition; links come from the document's relationships; text deleted
     under tracked changes is left out and inserted text kept. Title and
     author come from the document properties.
3. **Chapters.** A new chapter starts at each heading of the level the book
   is divided by: the highest level used more than once, or else the highest
   present. No headings, one chapter.
4. **The package.** One XHTML file a chapter, a navigation document, a
   stylesheet, a package document; written unzipped into the decompile
   stage's folder, with nothing in it that changes from run to run, so stage
   hashes repeat.
5. **Where it touches the code.** The decompile stage (one branch); the
   source checks in the CLI, the dashboard's upload and setup, and the book
   card (title and author from the new readers); the upload dialog's file
   types and wording.

**Kept:** text, headings, paragraphs, lists, quotations, emphasis, links.
**Not kept:** page layout, fonts, pictures, tables as tables, footnote
anchors, comments, tracked changes. The README must say so.

## 4. Other formats out: design

The compiled EPUB stays the one thing the pipeline builds and validates. The
other formats are made from it:

- **On request.** `book-agent export <workspace> --format docx|html|md|txt`,
  and `GET /api/jobs/<id>/output?format=...` behind a format choice beside
  the dashboard's Download button. Nothing in the config, so no stage hash
  changes and an EPUB job behaves as it does today.
- **For a converted source, also at compile.** A book that came as a Word
  document is written as one next to the EPUB, and recorded as an artifact.
- **How.** The EPUB is read back into the blocks of section 3, in spine
  order, and each writer walks them. Markdown escapes what it would read as
  markup, and keeps a heading or a list item on one line. The Word writer
  needs no dependency: seven small XML parts with heading, quotation, list,
  and preformatted styles, real lists, and hyperlinks, zipped with a fixed
  date so the same book gives the same file.

## 5. Open questions

1. **Pictures.** HTML and Markdown beside their image files could carry
   them; a file uploaded through the dashboard arrives alone. Word pictures
   would need the drawing relationships read.
2. **Footnotes** in Word documents: dropped, appended to the chapter, or
   turned into EPUB notes.
3. **The book's language tag.** The decompile stage does not know the job's
   source language; the package says `und` unless the file names one.
4. **Bilingual output** (source and translation side by side), which several
   of the projects above offer. A separate feature, and a natural one once
   blocks can be written in any format.
5. **Text PDF**, if wanted at all: as a converter outside the pipeline
   (PDF to Markdown, reviewed by a person, then in) and not a source format.

## 6. What it was tried on

No model was run for this: the formats change what goes into and comes out
of the pipeline, not the translation.

- **The fixture pipeline.** A book in each of the four formats goes through
  decompile, translate, audit, repair, compile, and validate with the test
  suite's stand-in models, and comes back as an EPUB and in its own format
  (`tests/test_book_formats.py`).
- **Whole real books.** Eight Project Gutenberg EPUBs (English, German,
  French, Spanish, Japanese, Chinese; up to *Don Quijote*, 5,395 blocks)
  were written in each format, read back, and turned into a package the
  pipeline's own reader accepted. Each conversion took about a second.
  Markdown, HTML, and Word came back block for block; text gives more
  paragraphs (a line of verse becomes a paragraph) and finds chapters by
  their headings' wording.
- **Word itself.** Word opened the document written for *Don Quijote*
  (5,395 paragraphs, its lists and links recognized). *The Sign of the Four*
  was opened in Word and saved again, which rewrites every style and list in
  Word's own form; read back, all 860 blocks were identical to the EPUB's.
- **The browser.** The Playwright suite adds a Markdown manuscript in the
  dashboard and downloads a finished book in each format
  (`frontend/e2e/formats.e2e.ts`).

What those trials changed in the first version: a heading or list item with
a line break in it broke Markdown; preformatted text lost its indentation;
Word lists were plain paragraphs with a typed bullet, and are real lists
now, each numbered list starting at one; Word links were dropped, and are
written and read; a monospaced word is read as code.

Not tried: a Word document with footnotes, tables, text boxes, or pictures
written by an author rather than by this code; an HTML page saved from the
web with its navigation and advertising; a text file in an encoding other
than UTF-8, UTF-16, GB18030, or CP1252.

## 7. Subtitles and game text: what stands in the way

Both are text to translate, and both break assumptions this pipeline makes
about a book.

### 7.1 Subtitles (SRT, ASS, VTT)

What carries over: the glossary, the translation contract, the language and
number checks, repair, the review queue, XLIFF export.

| Gap | Why it matters |
|---|---|
| The unit is a cue, not a paragraph | A sentence runs over several cues of one or two short lines. Translating cue by cue reads badly; translating the sentence means cutting it back into the same cues afterwards, which is a new alignment step |
| Hard limits on length | Characters a line, lines a cue, characters a second of screen time. The pipeline's length check compares a passage with what is usual in its document; it has no hard limit |
| Timing and markup must survive exactly | Timestamps, cue count, ASS override codes (`{\an8}`), positions. The inline-marker mechanism could protect them, but a contract for each format is needed |
| The output is not an EPUB | The compile and validate stages are written for one. Subtitles need their own writer and their own validation (same cues, same times), as RTF has |
| The rules for prose misfire | No chapters or headings; a dash marks a change of speaker; quotation conventions, the heading check, and the story summary do not apply |
| Less context | No narration and no speaker names; who is speaking, and to whom, decides the form of address in most languages |

Size: a new source adapter, one new stage-level check, a writer, and a
review view by cue. Roughly a phase of work, with most of the pipeline
reused.

### 7.2 Game text

What carries over: the glossary and the per-string contract, little else.

| Gap | Why it matters |
|---|---|
| Many formats, none of them a document | Ren'Py scripts, RPG Maker data, Translator++ projects, gettext PO, CSV, i18next JSON: each a parser and a writer. The projects that support them mostly read the output of an extraction tool, not the game |
| Strings without order or context | A menu label, a line from one branch of a dialogue, an item description. Chunking with neighbouring passages, the story context, and consistency over a chapter all assume one linear text |
| Placeholders and control codes | `{0}`, `%s`, `\V[1]`, colour tags, line breaks the engine counts. One lost or moved code breaks the game; each engine needs its own rule for what must survive |
| Space | A text box holds so many characters; the engine, not the reader, wraps the line |
| Repetition | The same string hundreds of times. It wants a translation memory, which the pipeline does not have |
| Plurals, gender, variables | PO plural forms; a sentence built around a name or a number filled in at run time |
| The checks | "Identical to the source" is right for a prose passage and wrong for a name, a key, or `OK` |
| Review | The dashboard shows chapters and passages; a string needs its key, its file, and where it appears |

Size: a different product, string localization, sharing the model client and
the glossary. If it is ever wanted, the cheaper route is interchange: read
and write XLIFF or PO, which the extraction tools already produce, and leave
the game formats to them.

## Sources

- [bilingual_book_maker](https://github.com/yihong0618/bilingual_book_maker)
- [Ebook Translator Calibre Plugin](https://github.com/bookfere/Ebook-Translator-Calibre-Plugin)
- [TranslateBooksWithLLMs](https://github.com/hydropix/TranslateBookWithLLM)
- [AiNiee](https://github.com/NEKOparapa/AiNiee)
- [epub-translator](https://github.com/oomol-lab/epub-translator)
- [booktrans](https://github.com/Wuxriff/booktrans)
- [Tolmach / book-translator](https://github.com/KazKozDev/book-translator)
- [ebook-GPT-translator](https://github.com/jesselau76/ebook-GPT-translator)
- [MoxHome](https://github.com/alexolvin/MoxHome)
