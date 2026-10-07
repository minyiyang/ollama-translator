# Benchmark books

The excerpts the benchmarks translate are not in this repository. This is the list of their sources
and how each excerpt is cut. To get them:

1. Download each source below into `sample/` at the repository root (git ignores that folder).
   From Project Gutenberg take the **EPUB3** file and keep the name the site gives it: it ends in
   `pg<number>-images-3.epub`, and the number is how the file is found. From 공유마당 take the
   `.txt` file and keep its name too.
2. From the repository root: `python lang_benchmark/make_books.py`

That writes the excerpts to `lang_benchmark/books/`, which git also ignores. It builds what is
missing and says which sources it could not find; `--force` rebuilds.

## Sources

| Excerpt | Work | Source | What is kept |
|---|---|---|---|
| `alice-ch1-4.epub` | Lewis Carroll, *Alice's Adventures in Wonderland* (1865) | [Project Gutenberg ebook 11](https://www.gutenberg.org/ebooks/11) | Chapters I to IV. 45,000 characters. |
| `alice-3ch.epub` | the same | the same | Chapters II, VIII, and IX. 37,000 characters. The en-zh regression book. |
| `sign-ch1-6.epub` | Arthur Conan Doyle, *The Sign of the Four* (1890) | [Project Gutenberg ebook 2097](https://www.gutenberg.org/ebooks/2097) | Chapters I to VI. 88,000 characters. |
| `wind-in-the-willows.epub` | Kenneth Grahame, *The Wind in the Willows* (1908) | [Project Gutenberg ebook 289](https://www.gutenberg.org/ebooks/289) | The whole book. 342,000 characters: hours of run time. |
| `verwandlung-1.epub` | Franz Kafka, *Die Verwandlung* (1915) | [Project Gutenberg ebook 22367](https://www.gutenberg.org/ebooks/22367) | Part I. 39,000 characters. |
| `meteore-ch1-3.epub` | Jules Verne, *La Chasse au météore* (1908) | [Project Gutenberg ebook 76724](https://www.gutenberg.org/ebooks/76724) | Chapters I to III. 65,000 characters. The file is 7 MB: it keeps the edition's plates. |
| `fortunata-1-2.epub` | Benito Pérez Galdós, *Fortunata y Jacinta* (1887) | [Project Gutenberg ebook 17013](https://www.gutenberg.org/ebooks/17013) | Part one, chapters I and II. 92,000 characters. |
| `rashomon.epub` | Akutagawa Ryūnosuke, 羅生門 (1915) | [Project Gutenberg ebook 1982](https://www.gutenberg.org/ebooks/1982) | The story with the file's header, without the licence pages. 7,000 characters. |
| `unsu-joeun-nal.epub` | Hyun Jin-geon, 운수 좋은 날 (1924) | [공유마당 (Korea Copyright Commission), 운수 좋은 날](https://gongu.copyright.or.kr/gongu/wrt/wrt/view.do?wrtSn=9002094&menuNo=200019): download the `.txt` file, `현진건-운수_좋은_날+B3356-개벽.txt`. Listed there as expired copyright, free to use; first printed in 개벽 no. 48, June 1924. | The whole story. 10,000 characters. |
| `ah-q-ch1-5.epub` | Lu Xun, 阿Q正传 (1921–22) | [Chinese Wikisource, 阿Q正傳](https://zh.wikisource.org/wiki/阿Q正傳), fetched by `make_books.py` in Simplified Chinese. Nothing to download. | Chapters 1 to 5. 11,000 characters. |

The character counts are of the excerpt's text as a reader would see it.

## Subtitles

There is no film here, so the subtitle file is made from a book:
`python lang_benchmark/make_subtitles.py` cuts "A Mad Tea-Party" (chapter VII
of *Alice's Adventures in Wonderland*, the same Project Gutenberg ebook 11)
into `lang_benchmark/books/alice-tea-party.srt`. What a character says is a
cue; what the narrator describes is a cue in italics; "said Alice" is left
out; two short lines by two speakers share a cue. Each cue is on screen for
as long as it takes to read at 17 characters a second. It comes to 214 cues
over twelve minutes. `--heading` picks another chapter, `--book` another
book, `--format vtt` WebVTT.

The first sixty cues are in the repository as `tests/data/alice-mad-tea-party.srt`,
for the tests.

## Rights

Each work is in the public domain in the United States, and each author died more than seventy
years ago (Carroll 1898, Verne 1905, Galdós 1920, Kafka 1924, Akutagawa 1927, Doyle 1930, Grahame
1932, Lu Xun 1936, Hyun 1943). That is not legal advice, and the rule differs by country.

Project Gutenberg files carry that project's licence and trademark terms, which is one reason they
are linked here and not copied. Wikisource and 공유마당 state the basis for each text on its page.

## Adding a book

Add a line to `EXCERPTS` in `make_books.py` and a row here.

- A Project Gutenberg EPUB with one chapter a file: `chapters(<number>, [<spine ids>])`.
  `python -m zipfile -l <file>` lists the files; the spine ids are in the `.opf`.
- One with the whole text in a single file: `section(<number>, <first heading>, <heading to stop at>)`,
  both regular expressions.
- Plain text: `build_epub` from `scripts/text_to_epub.py`; `join=""` for text wrapped at a fixed
  width inside words (Korean, Chinese).
- Chinese Wikisource: `page_text` from `fetch_wikisource.py`.

Then name the book for a pair in `run-benchmarks.ps1`, or pass it with `-Book`.
