import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test.describe("A subtitler works on a film's subtitles", () => {
  test("sees subtitle jobs beside books in the list, each named for what it is", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("columnheader", { name: "Kind" })).toBeVisible();
    const film = page.getByRole("row", { name: /^alice-film / });
    await expect(film).toContainText("alice-film.srt");
    await expect(film.getByRole("cell", { name: "Subtitles", exact: true })).toBeVisible();
    await expect(page.getByRole("row", { name: /^alice-german / }).getByRole("cell", { name: "Book", exact: true })).toBeVisible();
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
    await expect(page.getByText(/does not fit 2 lines of 42: \d+ characters/)).toBeVisible();
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

    // One thing comes out of a subtitle job, so there is no format to choose.
    const banner = page.getByRole("banner");
    await expect(banner.getByRole("combobox", { name: "Download format" })).toHaveCount(0);
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
  });
});
