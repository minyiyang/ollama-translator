# More book formats

Status: **implemented** for text, Markdown, HTML, and Word (.docx), as
sources and as extra outputs, and for PDFs that hold text, as sources
(`book_agent/book_formats.py`). This document
records what similar projects support, what was decided, how it is built,
what it was tried on, how subtitle jobs work, and what stands in the way of
game text.

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
| Scanned PDF | Skip: OCR is a different problem. Refused with that reason, also when a text layer lies under the scans |
| PDF that holds text | **Added** as a source (section 6.1). Never written |
| Subtitles | **Added** as a job of their own kind (section 7.1) |
| Game text | Not a book; section 7.2 lists what it would need |

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
3. ~~The book's language tag.~~ Settled: a converted book is tagged with
   the language its file names, or else the job's source language.
4. **Bilingual output** (source and translation side by side), which several
   of the projects above offer. A separate feature, and a natural one once
   blocks can be written in any format.
5. ~~Text PDF.~~ Added as a source (section 6.1).

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

Later trials, and what they changed:

- **A document typed in Word**, with a title, two headings, italics, a
  footnote, a numbered list, a table, a comment, a sentence typed with
  tracked changes on, and a text box (`tests/data/word-authored.docx`). It
  read as expected, the footnote and the comment left out, but the text box
  came out twice: Word stores one as a drawing and again as a fallback for
  older programs. The fallback is passed over now.
- **A page saved from the web**: Project Gutenberg's HTML of *Alice*, 820
  blocks and fifteen chapters, with the site's own lines before the first.
- **Series and XLIFF.** A finished subtitle job was added to a series and
  exported as XLIFF (230 units, a file for each part of the film).

Not tried: any of the four book formats with a model (the seven sample
files of `lang_benchmark/make_format_samples.py` are there for that); a
Word document with pictures; a text file in an encoding other than UTF-8,
UTF-16, GB18030, or CP1252.

### 6.1 PDF

A PDF that holds text is a source like the other four; one that is pictures
of pages is refused. It needs a library, `pdfminer.six` (MIT), the one
dependency this document's formats add: a PDF's text is compressed, encoded
font by font, and placed on the page glyph by glyph.

A PDF has no paragraphs, only lines at places on a page. The reader puts
them together again from how books are set:

- **Paragraphs.** A new one where a line is set in from the margin, where
  more room is left than between a paragraph's lines, or where the type
  changes. A book set without indents is broken where a short line ends a
  sentence. A paragraph runs on over a page break.
- **Headings.** Lines in type more than 15% larger than the body's, the
  largest size the highest level; and a short line that reads as a chapter
  heading. A heading on two lines is one heading. Of two headings that read
  the same, the later is the heading and the earlier a line of the contents
  page.
- **Left out.** Page numbers, and whatever stands at the top or bottom of
  a quarter of the pages.
- **Divided words.** "exam-" at the end of a line and "ple" on the next
  are joined when the book has "example" elsewhere and not "exam-ple";
  otherwise the hyphen stays, as in "three-dimensional".
- **Emphasis.** Italic and bold are read from the font's name.

**Limits.** Two columns, footnotes, tables, captions, and marginal notes are
read as paragraphs wherever the page puts them. Pictures are not carried
over. A scan with a text layer is refused when most pages are one large
picture; a scan made some other way would give that layer's misreadings.
Nothing is written as PDF: the translation is an EPUB, which
`book-agent export` writes in the other formats.

**Tried on:** two PDFs of the kind an ebook program makes, a novel of 60
pages and one of 62, each read in about five seconds: of the 854 paragraphs
read from *Alice's Adventures in Wonderland*, 759 were word for word a
paragraph of the EPUB of the same book, and its sixteen chapters were found.
(Those two files were a publisher's edition with its own terms, and were not
kept.) Two Google Books scans with a text layer were refused. A PDF saved by
Word from a short excerpt reads back exactly, and is the sample and the test
data (`tests/data/sign-of-the-four.pdf`). **Not tried:** a typeset book
with running heads and hyphenation, two columns, any language but English,
and a run with a model.

## 7. Subtitles and game text

Both are text to translate, and both break assumptions this pipeline makes
about a book. Subtitles are implemented, as a job of their own kind; game
text is not.

### 7.1 Subtitle jobs

**Implemented** for SubRip (`.srt`), WebVTT (`.vtt`), and Advanced SubStation
(`.ass`, `.ssa`) in `book_agent/subtitles.py`. A job's kind follows from its
source file; the server reports it as `job_type`, `book` or `subtitles`.

What a subtitle job reuses unchanged: the glossary, chunked translation and
its contract, the language and number checks, repair, the review queue, the
Text tab and its edits, XLIFF.

| What stood in the way | What was done |
|---|---|
| The unit is a cue, not a paragraph | A cue is one segment; its lines are joined to translate and broken again to write. Two lines that each open with a dash are two speakers and two segments. A cue without a letter in it (music) is not a segment and is written back as it was. A sentence over several cues is translated cue by cue: neighbouring cues are in the same chunk, so the model sees the sentence, and it is told never to move words between cues |
| Hard limits on length | `ReadingLimits`: characters a line, lines a cue, characters a second, by target language or from `subtitles:` in the config. The audit's new `readability` finding (code `unreadable_subtitle`) says a cue cannot be read in its time or does not fit the screen. Up to 1.3 times the reading speed it is a low finding; beyond, or when the lines do not fit, a medium one that goes to repair and then to review |
| Timing and markup must survive exactly | The file is kept as the pieces it is made of, and only a cue's text is replaced. Markup around a whole cue is put back around its translation. The validate stage reads the written file back: the same number of cues, each at its time, with its markup and the accepted text |
| The output is not an EPUB | The compile and validate stages have a third branch beside EPUB and RTF. The output is a file of the kind that went in; there is no export to other formats |
| The rules for prose misfire | A scene of ten cues with short answers ("Yes.", "Yeah.", a name alone) gave no finding from the existing rules. The translation prompt replaces the config's prose style with one for subtitles. The story context, the style sheet, and the prose rewrite are not adapted and are best left off |
| Less context | Not addressed. Who is speaking is not known, apart from a WebVTT voice tag, which is kept but not used |

Where a book has chapters, a subtitle file has parts: a new one after four
seconds without a cue once a part has forty cues, and at a hundred and fifty
at the latest. They are named by their times.

**Limits.** Markup inside a cue (one word in italics, a karaoke timing) is
dropped from a translated cue. Timing is never changed, so a translation
that needs longer on screen can only be shortened. The reading check counts
characters, not their width. An `.ass` file's `Format` line must name `Text`
last, as the format requires. Two files for one film (a forced-narrative
track) are two jobs. No bilingual output.

**Tried on:** hand-written files in the three formats, round-tripped
unchanged byte for byte; a ten-cue scene through the fixture pipeline; in
the browser, a subtitle job reviewed, compiled, and downloaded; and "A Mad
Tea-Party" from *Alice's Adventures in Wonderland* cut into 214 cues of
speech, narration, and two-speaker lines (`lang_benchmark/make_subtitles.py`),
its first sixty kept as test data (`tests/test_subtitles.py`,
`frontend/e2e/subtitles.e2e.ts`). That real text found two faults in the
line breaking, both fixed: a second line longer than the limit, and a word
broken in two.

**A run with the models** (English into German, translategemma for the
translation, gemma4:31b for audit and repair) took that scene to a finished
file: 214 cues at their times, markup and two-speaker cues intact, three
cues shortened by hand in the final review. What four runs of it changed:

- The audit read one cue to a call and took 2 hours 23 minutes. It reads
  eight to a call now, with a small fixed allowance of output, and took 12
  minutes.
- The auditor judged cues as a book's prose and repair lengthened them. Both
  are told they are reading subtitles, and repair not to make a cue longer.
- A batch the model could not answer for cost every cue in it a false
  finding. It is audited again in halves.
- A speaker's line too long for the screen was missed when the cue was also
  slow to read. The two are separate findings.

**The models' own weaknesses**, recorded and left: the glossary chose
"Maus" for Dormouse; repair wrote German that is not grammatical ("reingetan
sollen", "mit Rätsel") and the verifier passed it; and a call now and then
repeats itself until its allowance runs out, which the fixed allowance
bounds but does not prevent. When the GPU's memory is short the same model
runs at a fifth of its speed without saying so.

**Not tried:** a whole film's file, a file made by a subtitling tool,
WebVTT or ASS with a model, and subtitles into Chinese, Japanese, or
Korean. The reading limits are the published guidelines' as remembered and
have not been checked against a source or against real translations.

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
