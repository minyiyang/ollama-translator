import { expect, test } from "@playwright/test";

test.describe("An editor reads the book side by side", () => {
  test("finds a passage by a word in either language", async ({ page }) => {
    await page.goto("/jobs/alice-german/text");
    await expect(page.getByText("5 of 5 segments")).toBeVisible();
    // Each side is marked with its own language, so the browser hyphenates and reads German as German.
    await expect(page.getByRole("cell", { name: "Der Tränenteich" })).toHaveAttribute("lang", "de");
    await expect(page.getByRole("cell", { name: "The Pool of Tears", exact: true })).toHaveAttribute("lang", "en");
    const search = page.getByRole("textbox", { name: "Search source or translation" });
    await search.fill("Kaninchen");
    await expect(page.getByText("1 of 5 segments")).toBeVisible();
    await expect(page.getByRole("cell", { name: /The White Rabbit came trotting back/ })).toBeVisible();
    await search.fill("lonely");
    await expect(page.getByRole("cell", { name: /sehr einsam und niedergeschlagen/ })).toBeVisible();
  });

  test("sees which passage is waiting in the review queue and why", async ({ page }) => {
    await page.goto("/jobs/alice-german/text");
    await page.getByRole("radio", { name: "In review queue" }).click();
    await expect(page.getByText("1 of 5 segments")).toBeVisible();
    await expect(page.getByText(/numeric content differs from source/)).toBeVisible();
    await page.getByRole("link", { name: /Open in Final review/ }).click();
    await expect(page).toHaveURL(/\/jobs\/alice-german\/review$/);
  });

  test("rewords a sentence and finds it again under Edited", async ({ page }) => {
    await page.goto("/jobs/alice-german-text/text");
    const row = page.getByRole("row", { name: /Alice began to cry again/ });
    await row.getByRole("button", { name: "Edit" }).click();
    await page.locator("textarea").fill("Alice begann wieder zu weinen, denn sie fühlte sich sehr einsam und mutlos.");
    const save = page.getByRole("button", { name: "Save", exact: true });
    await expect(save).toBeDisabled(); // a change to the book needs its reason
    await page.getByPlaceholder("Why you are making this change").fill("Reads more naturally.");
    await save.click();
    await expect(page.getByRole("cell", { name: /sehr einsam und mutlos/ })).toBeVisible();
    await page.getByRole("radio", { name: "Edited" }).click();
    await expect(page.getByText("1 of 5 segments")).toBeVisible();
    // Still there after the page is loaded again.
    await page.reload();
    await expect(page.getByRole("cell", { name: /sehr einsam und mutlos/ })).toBeVisible();
  });
});
