import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

// e2e/serve.py writes this book when it starts: one the dashboard does not have yet.
const BOOK = fileURLToPath(new URL("./.books/the-sign-of-the-four.epub", import.meta.url));

test.describe("A translator adds a book", () => {
  test("uploads it, keeps the suggested name, and chooses its languages before anything runs", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("button", { name: "+ Add new job" }).click();
    const dialog = page.getByRole("dialog", { name: "New translation job" });
    await expect(dialog.getByRole("button", { name: "Create job" })).toBeDisabled();

    await dialog.locator("input[type=file]").setInputFiles(BOOK);
    await expect(dialog.getByRole("textbox", { name: "Config file name" })).toHaveValue(/the-sign-of-the-four/);
    await dialog.getByRole("button", { name: "Create job" }).click();

    await expect(page).toHaveURL(/\/jobs\/the-sign-of-the-four[^/]*\/config$/);
    await expect(page.getByText("The Sign of the Four").first()).toBeVisible();
    await expect(page.getByText("Arthur Conan Doyle").first()).toBeVisible();

    // They want it in Japanese, which the pipeline knows less well than Chinese, and are told so.
    const into = page.getByRole("combobox", { name: "Into language" });
    await into.fill("ja");
    await into.blur();
    await expect(page.getByText(/English → Japanese/).first()).toBeVisible();
    await expect(page.getByText(/Quality depends on the local model/)).toBeVisible();

    // Back in the list the new book is there, and nothing has started.
    await page.getByRole("link", { name: "Ollama Translator" }).click();
    await expect(page.getByRole("row", { name: /^the-sign-of-the-four/ })).toContainText("draft");
  });
});
