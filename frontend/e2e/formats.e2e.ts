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
