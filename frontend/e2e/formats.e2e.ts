import { expect, test } from "@playwright/test";
import { readFile } from "node:fs/promises";

test.describe("A translator works with files that are not EPUBs", () => {
  test("is told which kinds of book can be added, and that a Kindle file is not one of them", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "+ Add new job" }).click();
    const dialog = page.getByRole("dialog", { name: "New translation job" });
    await expect(dialog.getByText(/Drop a book .* or a subtitle file .* here, or/)).toBeVisible();
    await dialog.locator("input[type=file]").setInputFiles({ name: "notes.mobi", mimeType: "application/x-mobipocket-ebook", buffer: Buffer.from("BOOKMOBI") });
    await expect(page.getByRole("status")).toContainText("Choose a book (EPUB, RTF, text, Markdown, HTML, Word .docx, PDF) or a subtitle file (.srt, .vtt, .ass).");
    await expect(dialog.getByRole("button", { name: "Create job" })).toBeDisabled();
  });

  test("adds a Markdown manuscript and sees its title and author read from the file", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "+ Add new job" }).click();
    const dialog = page.getByRole("dialog", { name: "New translation job" });
    const manuscript = [
      "---", "title: The Sign of the Four", "author: Arthur Conan Doyle", "---", "",
      "## Chapter I. The Science of Deduction", "",
      "Sherlock Holmes took his bottle from the corner of the *mantelpiece*.", "",
    ].join("\n");
    await dialog.locator("input[type=file]").setInputFiles({ name: "sign-of-the-four.md", mimeType: "text/markdown", buffer: Buffer.from(manuscript) });
    await expect(dialog.getByText("The Sign of the Four").first()).toBeVisible();
    await expect(dialog.getByText("Arthur Conan Doyle").first()).toBeVisible();
    await expect(dialog.getByRole("textbox", { name: "Config file name" })).toHaveValue(/sign-of-the-four/);
    await dialog.getByRole("button", { name: "Create job" }).click();
    await expect(page).toHaveURL(/\/jobs\/sign-of-the-four[^/]*\/config$/);
  });

  test("takes a finished book as a Word document, a web page, Markdown, or plain text", async ({ page }) => {
    await page.goto("/jobs/alice-manuscript-finished/progress");
    const banner = page.getByRole("banner");
    await expect(banner).toContainText("complete");
    const format = banner.getByRole("combobox", { name: "Download format" });
    for (const [label, suffix] of [["EPUB", ".epub"], ["Word (.docx)", ".docx"], ["HTML", ".html"], ["Markdown", ".md"], ["Plain text", ".txt"]]) {
      await format.selectOption({ label });
      const download = page.waitForEvent("download");
      await banner.getByRole("link", { name: /Download/ }).click();
      const file = await download;
      expect(file.suggestedFilename()).toMatch(new RegExp(`^alice-manuscript\\.translated-.*\\${suffix}$`));
      if (suffix === ".md") {
        // The manuscript came as Markdown and comes back as Markdown, in German.
        const text = await readFile(await file.path(), "utf-8");
        expect(text).toContain("# Der Tränenteich");
        expect(text).toContain("Alice öffnete die kleine Tür um 3 Uhr");
      }
    }
  });
});

test.describe("A translator's manuscript has a picture and a footnote", () => {
  test("sees on Progress what translating the title and the note cost, as for every other stage", async ({ page }) => {
    await page.goto("/jobs/alice-manuscript-finished/progress");
    const title = page.getByRole("row", { name: /Translate title/ });
    // The run asked the model three times at this stage, the title, the note, and the picture's
    // description, and logged what each gave back.
    await expect(title).toContainText("Alice im Wunderland; 1 of 1 notes translated; 1 of 1 picture descriptions translated");
    await expect(title.locator("td.num").nth(0)).toContainText("3");
    await expect(title.locator("td.num").nth(1)).toHaveText("46");
    await expect(title.locator("td.work")).toContainText("3 LLM tasks");
  });

  test("downloads the book with its picture and its note in German, in each format", async ({ page }) => {
    await page.goto("/jobs/alice-manuscript-finished/progress");
    const banner = page.getByRole("banner");
    await expect(banner).toContainText("complete");
    const format = banner.getByRole("combobox", { name: "Download format" });
    const download = async (label: string) => {
      await format.selectOption({ label });
      const started = page.waitForEvent("download");
      await banner.getByRole("link", { name: /Download/ }).click();
      return readFile(await (await started).path());
    };

    const html = (await download("HTML")).toString("utf-8");
    expect(html).toContain('alt="Eine Karte des Gartens"'); // the picture's description, in German too
    expect(html).toMatch(/<img src="data:image\/png;base64,[A-Za-z0-9+/=]{100,}"/); // the picture is in the page itself
    expect(html).toContain('<a href="#note-1">1</a>');
    expect(html).toContain("Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht.");

    const markdown = (await download("Markdown")).toString("utf-8");
    expect(markdown).toContain("in der einen Hand.[^1]");
    expect(markdown).toContain("[^1]: Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht.");
    expect(markdown).toContain("![Eine Karte des Gartens](data:image/png;base64,");

    // A Word document keeps the picture as a picture and the note as one of Word's own footnotes.
    const word = await download("Word (.docx)");
    expect(word.includes(Buffer.from("word/media/garden.png"))).toBe(true);
    expect(word.includes(Buffer.from("word/footnotes.xml"))).toBe(true);

    const text = (await download("Plain text")).toString("utf-8");
    expect(text).toContain("in der einen Hand.[1]");
    expect(text).toContain("[1] Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht.");
  });
});
