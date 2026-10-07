import { expect, test } from "@playwright/test";

test.describe("Someone looks into a job that is waiting for them", () => {
  test("learns from Progress why the run stopped and where to go", async ({ page }) => {
    await page.goto("/jobs/alice-german/progress");
    await expect(page.getByText("16 of 18 stages complete")).toBeVisible();
    // The book's title was no passage of the chapter: the title stage translated it, and says what it chose.
    await expect(page.getByRole("row", { name: /Translate title/ })).toContainText("Alice im Wunderland");
    await expect(page.getByText(/1 unresolved defect\(s\)/).first()).toBeVisible();
    await expect(page.getByRole("row", { name: /Translate/ }).first()).toContainText("done");
    await page.getByRole("link", { name: "Open final review" }).click();
    await expect(page).toHaveURL(/\/review$/);
  });

  test("finds the names the book uses in the Glossary, in the book's two languages", async ({ page }) => {
    await page.goto("/jobs/alice-german/glossary");
    await expect(page.getByRole("columnheader", { name: "English" })).toBeVisible();
    await expect(page.getByRole("columnheader", { name: "German" })).toBeVisible();
    await expect(page.getByRole("row", { name: /White Rabbit Weißes Kaninchen/ })).toBeVisible();
    await page.getByRole("textbox", { name: /Search/ }).fill("Kaninchen");
    await expect(page.getByText("1 of 2 terms")).toBeVisible();
    await expect(page.getByRole("row", { name: /^Alice Alice/ })).toHaveCount(0);
  });

  test("can read how the run was set up but not change it, and can still fold the sections away", async ({ page }) => {
    await page.goto("/jobs/alice-german/config");
    await expect(page.getByText("Alice's Adventures in Wonderland").first()).toBeVisible();
    await expect(page.getByText(/it is read-only/)).toBeVisible();
    const into = page.getByRole("combobox", { name: "Into language" });
    await expect(into).toBeDisabled();
    await expect(into).toHaveValue("de");
    const translation = page.getByRole("button", { name: /Translation .* settings/ });
    await translation.click();
    await expect(into).toBeHidden();
    await translation.click();
    await expect(into).toBeVisible();
  });
});
