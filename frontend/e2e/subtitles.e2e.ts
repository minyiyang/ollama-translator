import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test.describe("A subtitler works on a film's subtitles", () => {
  test("sees subtitle jobs beside books in the list, each named for what it is", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("columnheader", { name: "Kind" })).toBeVisible();
    const film = page.getByRole("row", { name: /^alice-film / });
    await expect(film).toContainText("alice-film.srt");
    await expect(film.getByRole("cell", { name: "Subtitles", exact: true })).toBeVisible();
    await expect(page.getByRole("row", { name: /^alice-manuscript-finished / }).getByRole("cell", { name: "Book", exact: true })).toBeVisible();
  });

  test("takes a finished film from the list in the subtitle format wanted", async ({ page }) => {
    await page.goto("/");
    const film = page.getByRole("row", { name: /^alice-film-finished / });
    const format = film.getByRole("combobox", { name: "Download format" });
    await expect(format.getByRole("option")).toHaveText(["SRT", "WebVTT", "ASS"]);
    // The menu stands to the left of the link, on its line.
    const menu = (await format.boundingBox())!;
    const link = (await film.getByRole("link", { name: /Download/ }).boundingBox())!;
    expect(menu.x + menu.width).toBeLessThanOrEqual(link.x);
    expect(Math.abs(menu.y + menu.height / 2 - (link.y + link.height / 2))).toBeLessThan(6);
    await format.selectOption("WebVTT");
    const download = page.waitForEvent("download");
    await film.getByRole("link", { name: /Download/ }).click();
    expect((await download).suggestedFilename()).toMatch(/^alice-film\.translated-.*\.vtt$/);
    // A book in the list has the book formats.
    const book = page.getByRole("row", { name: /^alice-manuscript-finished / });
    await expect(book.getByRole("combobox", { name: "Download format" }).getByRole("option").first()).toHaveText("EPUB");
  });

  test("adds a subtitle file and is told how much there is of it", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "+ Add new job" }).click();
    const dialog = page.getByRole("dialog", { name: "New translation job" });
    const file = [
      "1", "00:00:01,000 --> 00:00:03,500", "Which is it today,", "morphine or cocaine?", "",
      "2", "00:01:04,000 --> 00:01:06,000", "It is cocaine.", "",
    ].join("\n");
    await dialog.locator("input[type=file]").setInputFiles({ name: "hound-of-the-baskervilles.srt", mimeType: "application/x-subrip", buffer: Buffer.from(file) });
    await expect(dialog.getByText("2 cues, to 0:01:06")).toBeVisible();
    await expect(dialog.getByRole("textbox", { name: "Config file name" })).toHaveValue(/hound-of-the-baskervilles/);
    await dialog.getByRole("button", { name: "Create job" }).click();
    await expect(page).toHaveURL(/\/jobs\/hound-of-the-baskervilles[^/]*\/config$/);
    await page.getByRole("link", { name: "Ollama Translator" }).click();
    await expect(page.getByRole("row", { name: /^hound-of-the-baskervilles/ }).getByRole("cell", { name: "Subtitles", exact: true })).toBeVisible();
  });

  test("reads the film cue by cue, a part of the film where a book has a chapter", async ({ page }) => {
    await page.goto("/jobs/alice-film/text");
    await expect(page.getByRole("button", { name: /0:00:01 – 0:00:17/ })).toBeVisible();
    // Two speakers in one cue are two passages; the music has nothing to translate and is not listed.
    await expect(page.getByRole("cell", { name: "Who are you?", exact: true })).toBeVisible();
    await expect(page.getByRole("cell", { name: "Ich weiß es kaum, mein Herr.", exact: true })).toBeVisible();
    await expect(page.getByText("5 of 5 segments")).toBeVisible();
    // Each passage says when its cue comes on screen and for how long.
    await expect(page.getByRole("row", { name: /Alice began to cry again/ })).toContainText("0:00:14 · 3.0 s");
  });

  test("is warned while rewording a cue that it has become too long to read or to fit", async ({ page }) => {
    await page.goto("/jobs/alice-film/review");
    await expect(page.getByText("0:00:01 · 3.0 s")).toBeVisible();
    const editor = page.getByRole("textbox").first();
    await editor.fill("Alice öffnete um 3 Uhr die kleine Tür und blickte durch den Gang in den schönsten Garten, den man je gesehen hat.");
    await expect(page.getByText(/too long to read in 3\.0 s: \d+ characters, about 60 can be read/)).toBeVisible();
    await expect(page.getByText(/does not fit 2 lines of 42: it needs 3/)).toBeVisible();
    await editor.fill("Alice öffnete die kleine Tür um 3 Uhr.");
    await expect(page.getByText(/too long to read|does not fit/)).toHaveCount(0);
  });

  test("corrects a cue in the final review, compiles, and gets the subtitle file back with its times untouched", async ({ page }) => {
    await page.goto("/jobs/alice-film/review");
    const editor = page.getByRole("textbox").first();
    await expect(editor).toHaveValue("Alice öffnete die kleine Tür um 5 Uhr.");
    await editor.fill("Alice öffnete die kleine Tür um 3 Uhr.");
    await page.getByRole("radio", { name: "Corrected the mistranslation identified by the audit." }).check();
    await page.getByRole("button", { name: "Replace with edit" }).click();
    await page.getByRole("button", { name: "Apply decisions" }).click();
    await expect(page.getByText("All decisions applied.")).toBeVisible();
    await page.getByRole("button", { name: "Compile now" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Compile finished: complete" })).toBeVisible({ timeout: 60_000 });

    // What comes out of a subtitle job is a subtitle file: in the format it came in, or another, and in no book's.
    const banner = page.getByRole("banner");
    const format = banner.getByRole("combobox", { name: "Download format" });
    await expect(format).toHaveValue("srt");
    await expect(format.getByRole("option")).toHaveText(["SRT", "WebVTT", "ASS"]);
    const download = page.waitForEvent("download");
    await banner.getByRole("link", { name: /Download/ }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/^alice-film\.translated-.*\.srt$/);
    // The file keeps the line endings its source had, which on Windows are two characters.
    const text = (await readFile(await file.path(), "utf-8")).replace(/\r\n/g, "\n");
    expect(text).toContain("1\n00:00:01,000 --> 00:00:04,000\nAlice öffnete die kleine Tür um 3 Uhr.\n");
    expect(text).toContain("2\n00:00:05,000 --> 00:00:08,000\n- Wer bist du?\n- Ich weiß es kaum, mein Herr.\n");
    expect(text).toContain("3\n00:00:09,000 --> 00:00:11,500\n<i>Das Weiße Kaninchen kam zurückgetrabt.</i>\n");
    expect(text).toContain("4\n00:00:12,000 --> 00:00:13,000\n♪ ♪\n");

    // The same cues at the same times as WebVTT, italics kept.
    await format.selectOption("WebVTT");
    const second = page.waitForEvent("download");
    await banner.getByRole("link", { name: /Download/ }).click();
    const converted = await second;
    expect(converted.suggestedFilename()).toMatch(/^alice-film\.translated-.*\.vtt$/);
    const vtt = await readFile(await converted.path(), "utf-8");
    expect(vtt.startsWith("WEBVTT\n\n00:00:01.000 --> 00:00:04.000\nAlice öffnete die kleine Tür um 3 Uhr.\n")).toBe(true);
    expect(vtt).toContain("00:00:05.000 --> 00:00:08.000\n- Wer bist du?\n- Ich weiß es kaum, mein Herr.\n");
    expect(vtt).toContain("00:00:09.000 --> 00:00:11.500\n<i>Das Weiße Kaninchen kam zurückgetrabt.</i>\n");

    // An ASS file gets a style the source never had, and holds hundredths of a second: that is said, not hidden.
    await format.selectOption("ASS");
    const third = page.waitForEvent("download");
    await banner.getByRole("link", { name: /Download/ }).click();
    expect((await third).suggestedFilename()).toMatch(/\.ass$/);
    await expect(page.getByRole("status").filter({ hasText: "Downloading as ASS, which is not the file's own format: one default style is added" })).toBeVisible();
  });

  test("is shown a subtitle file's pipeline and settings, with no book or EPUB in them", async ({ page }) => {
    await page.goto("/jobs/alice-film-finished/progress");
    const stages = page.getByRole("table").last();
    await expect(stages.getByText("Write the subtitle file", { exact: true })).toBeVisible();
    await expect(stages.getByText("Check the subtitle file", { exact: true })).toBeVisible();
    // A subtitle file has no title to translate: the stage that does that for a book is not listed.
    await expect(page.getByText("Translate title")).toHaveCount(0);
    await expect(page.getByText(/EPUB|Build the book|Check the book/)).toHaveCount(0);

    await page.goto("/jobs/alice-film-finished/review");
    await expect(page.getByText(/EPUB|Build the book/)).toHaveCount(0);

    await page.goto("/jobs/alice-film-finished/config");
    await expect(page.getByText("subtitles.line_characters", { exact: true })).toBeVisible();
    await expect(page.getByText(/Settings only a book uses .* are not shown for a subtitle job/)).toBeVisible();
    for (const setting of ["reprose.enabled", "epub.strip_print_page_markers", "output.pdf_font", "translation.translated_title"]) {
      await expect(page.getByText(setting, { exact: true })).toHaveCount(0);
    }
    const output = page.locator(".opt", { has: page.getByText("output.format", { exact: true }) });
    await expect(output.getByRole("option")).toHaveText(["source", "srt", "vtt", "ass"]);

    // A book's config has the book's settings and not the reading limits.
    await page.goto("/jobs/alice-german/config");
    await expect(page.getByText("epub.strip_print_page_markers", { exact: true })).toBeVisible();
    await expect(page.getByText("subtitles.line_characters", { exact: true })).toHaveCount(0);
  });
});
