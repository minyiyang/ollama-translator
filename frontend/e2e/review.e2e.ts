import { expect, test, type Page } from "@playwright/test";

const editor = (page: Page) => page.getByRole("textbox").first();

test.describe("A reviewer works through the final review", () => {
  test("is shown the passage, what the book says, and what the audit found", async ({ page }) => {
    await page.goto("/jobs/alice-german/review");
    await expect(page.getByText("1 of 1")).toBeVisible();
    await expect(page.getByText("Alice opened the little door at 3 o'clock").first()).toBeVisible();
    await expect(editor(page)).toHaveValue(/Alice öffnete die kleine Tür um 5 Uhr/);
    await expect(page.getByText("The source says 3 o'clock; the translation says 5.").first()).toBeVisible();
    await expect(page.getByText(/numeric content differs from source/).first()).toBeVisible();
  });

  test("is not told a German translation still has English in it", async ({ page }) => {
    await page.goto("/jobs/alice-german/review");
    await expect(editor(page)).toHaveValue(/Alice öffnete/);
    // German and English are written in the same letters: the page cannot tell them apart and must not guess.
    await expect(page.getByText(/English left in text/)).toHaveCount(0);
    await expect(page.getByText("ZH:")).toHaveCount(0);
    await expect(page.getByRole("radio", { name: /English/ })).toHaveCount(0); // nor offered a reason about English
    await expect(editor(page)).toHaveAttribute("lang", "de");
  });

  test("is told when English is left in a Chinese translation", async ({ page }) => {
    await page.goto("/jobs/alice-chinese/review");
    await expect(editor(page)).toHaveValue(/爱丽丝在5点钟打开了那扇小门/);
    await editor(page).fill("爱丽丝在3点钟打开了 the little door。");
    await expect(page.getByText(/English left in text: the, little, door/)).toBeVisible();
  });

  test("cannot wave a passage through without saying why", async ({ page }) => {
    await page.goto("/jobs/alice-german-accept/review");
    const accept = page.getByRole("button", { name: /Accept current|Select or enter a reason first/ });
    await expect(accept).toBeDisabled();
    await page.getByRole("radio", { name: "Custom reason" }).check();
    await page.getByPlaceholder(/source-grounded reason/).fill("ok");
    await expect(accept).toBeDisabled(); // two letters are not a reason
    await page.getByPlaceholder(/source-grounded reason/).fill("The hour is five in this edition of the book.");
    await expect(accept).toBeEnabled();
  });

  test("corrects the wrong hour, applies the decision, compiles, and gets the book", async ({ page }) => {
    await page.goto("/jobs/alice-german-rewrite/review");
    const current = await editor(page).inputValue();

    // A slip while typing: another wrong hour is pointed out before anything is saved.
    await editor(page).fill(current.replace("5 Uhr", "7 Uhr"));
    await expect(page.getByText(/digits changed from current/)).toBeVisible();

    await editor(page).fill(current.replace("5 Uhr", "3 Uhr"));
    await page.getByRole("radio", { name: "Corrected the mistranslation identified by the audit." }).check();
    await page.getByRole("button", { name: "Replace with edit" }).click();
    await expect(page.getByText("1/1 decided")).toBeVisible();

    await page.getByRole("button", { name: "Apply decisions" }).click();
    await expect(page.getByText("All decisions applied.")).toBeVisible();
    await expect(page.getByText("Nothing left in the review queue.")).toBeVisible();

    await page.getByRole("button", { name: "Compile now" }).click();
    await expect(page.getByRole("status").filter({ hasText: "Compile finished: complete" })).toBeVisible({ timeout: 60_000 });
    await expect(page.getByRole("banner")).toContainText("complete");

    // The book can be downloaded, and the corrected hour is in its text.
    const download = page.waitForEvent("download");
    await page.getByRole("banner").getByRole("link", { name: /Download/ }).click();
    expect((await download).suggestedFilename()).toMatch(/\.epub$/);
    await page.getByRole("link", { name: "Text", exact: true }).click();
    await expect(page.getByRole("cell", { name: /Alice öffnete die kleine Tür um 3 Uhr/ })).toBeVisible();
  });

  test("finds nothing to do on a book that is already finished", async ({ page }) => {
    await page.goto("/jobs/alice-german-finished/review");
    await expect(page.getByText("This job is complete; there is nothing left to review.")).toBeVisible();
  });
});
