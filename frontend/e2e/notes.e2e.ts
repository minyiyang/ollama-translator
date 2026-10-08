import { expect, test, type Page } from "@playwright/test";

// alice-german-finished is chapter II of Alice as a publisher set it: a footnote beside the
// text, an endnote in a document of its own, and a picture of the White Rabbit. The title
// stage gave the footnote, the contents, and the picture's description in German, and left the
// endnote in English: the model answered it without its link back. The proofreader had worded
// the picture's description before the stage's last run, which worded it otherwise: their
// edit is a conflict, still in the book until they resolve it.
//
// The tests take the job from there one after another, as one proofreader would.
test.describe.configure({ mode: "serial" });

const JOB = "/jobs/alice-german-finished";
const html = async (page: Page) => (await page.request.get(`/api${JOB}/output?format=html`)).text();

async function recompile(page: Page, edits: number) {
  await expect(page.getByText(`${edits} edit${edits === 1 ? "" : "s"} not in the compiled book.`)).toBeVisible();
  await page.getByRole("button", { name: "Recompile" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Recompile" }).click();
}

test.describe("A proofreader checks the notes and pictures of a finished book", () => {
  test("reads the footnote under the sentence that refers to it, and sees the picture with its German description", async ({ page }) => {
    await page.goto(`${JOB}/text`);
    const sidebar = page.getByRole("complementary");
    // What the book says of itself comes first: its title and contents.
    await expect(sidebar.getByRole("button", { name: /Title and contents/ })).toBeVisible();
    await sidebar.getByRole("button", { name: /The Pool of Tears/ }).click();

    const rabbit = page.getByRole("row", { name: /The White Rabbit came trotting back/ }).first();
    await rabbit.getByRole("button", { name: "Note 1" }).click();
    const peek = page.locator("tr.note-peek");
    await expect(peek).toContainText("Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht.");
    await peek.getByRole("button", { name: "Go to the note" }).click();
    await expect(page.locator("#row-D0000-N000001")).toHaveClass(/focus/);
    await expect(page.locator("#row-D0000-N000001")).toBeInViewport();

    // The picture, served from the book itself, after the passage it follows.
    const picture = page.getByRole("img", { name: "The White Rabbit with his gloves" });
    await expect(picture).toBeVisible();
    expect(await picture.evaluate((image: HTMLImageElement) => image.naturalWidth)).toBe(240);
    await expect(page.locator("tr", { has: picture })).toContainText("Das Weiße Kaninchen mit seinen Handschuhen");
  });

  test("follows a reference to the endnote in the endnotes, and back to the sentence", async ({ page }) => {
    await page.goto(`${JOB}/text?chapter=chapter`);
    const crying = page.getByRole("row", { name: /Alice began to cry again/ }).first();
    await crying.getByRole("button", { name: "Note →" }).click();
    const endnote = page.locator("#row-D0001-N000002");
    await expect(endnote).toHaveClass(/focus/);
    await expect(endnote).toContainText("The pool was made of the tears Alice had wept.");
    await expect(endnote).toContainText("untranslated");

    await endnote.getByRole("button", { name: "↑ D0000-S000004" }).click();
    await expect(page.locator("#row-D0000-S000004")).toHaveClass(/focus/);
  });

  test("sees beside each chapter what it holds and what waits, and the book's totals", async ({ page }) => {
    await page.goto(`${JOB}/text`);
    const sidebar = page.getByRole("complementary");
    await expect(sidebar.getByRole("button", { name: /The Pool of Tears/ })).toContainText("1 conflict · 1 note");
    await expect(sidebar.getByRole("button", { name: /Endnotes/ })).toContainText("1 untranslated · 2 notes");
    const totals = page.locator(".stats");
    await expect(totals.locator(".stat", { hasText: "notes" })).toHaveText("3notes");
    await expect(totals.locator(".stat", { hasText: "untranslated" })).toHaveText("1untranslated");
    await expect(totals.locator(".stat", { hasText: "conflicts" })).toHaveText("1conflicts");
  });

  test("follows the title stage's row on Progress to what it left in English", async ({ page }) => {
    await page.goto(`${JOB}/progress`);
    const title = page.getByRole("row", { name: /Translate title/ });
    await title.getByRole("link", { name: "1 left in the source language: translate on Text →" }).click();
    await expect(page).toHaveURL(new RegExp(`${JOB}/text\\?view=untranslated$`));
    await expect(page.getByRole("radio", { name: "Untranslated" })).toHaveAttribute("aria-checked", "true");
  });

  test("filters the endnotes to what is still in English", async ({ page }) => {
    await page.goto(`${JOB}/text?chapter=endnotes`);
    await expect(page.locator("#row-D0001-N000001")).toBeVisible(); // the endnotes' heading, in German
    await page.getByRole("radio", { name: "Untranslated" }).click();
    await expect(page.getByText("1 of 2 segments")).toBeVisible();
    await expect(page.locator("#row-D0001-N000002")).toBeVisible();
    await expect(page.locator("#row-D0001-N000001")).toHaveCount(0);
  });

  test("is sent from the review page to the endnote left in English, translates it, and gets it in the book", async ({ page }) => {
    await page.goto(`${JOB}/review`);
    const card = page.getByRole("region", { name: "Left in the source language" });
    await expect(card).toContainText("The pool was made of the tears Alice had wept.");
    await card.getByRole("row", { name: /The pool was made of the tears/ }).getByRole("link", { name: "Translate on Text →" }).click();

    await expect(page).toHaveURL(/\/text\?chapter=endnotes&item=D0001-N000002$/);
    const endnote = page.locator("#row-D0001-N000002");
    await expect(endnote).toHaveClass(/focus/);
    await endnote.getByRole("button", { name: "Edit" }).click();
    const editor = page.locator("textarea.editor");
    // Without its link back the note could not be put back as the book has it: the check says so.
    await editor.fill("Der Teich war aus den Tränen, die Alice geweint hatte. Zurück");
    await expect(page.getByText(/Keep the note's link and emphasis markers/)).toBeVisible();
    await editor.fill("Der Teich war aus den Tränen, die Alice geweint hatte. <I000>Zurück</I000>");
    await expect(page.getByText("Passes deterministic validation.")).toBeVisible();
    await page.getByPlaceholder("Why you are making this change").fill("The model dropped the link back");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(endnote).toContainText("edited");

    await recompile(page, 1);
    // The book as it is downloaded has the endnote in German, its link back in place.
    await expect.poll(() => html(page), { timeout: 60_000 }).toContain("Der Teich war aus den Tränen, die Alice geweint hatte.");
    const book = await html(page);
    expect(book).toContain("Glacéhandschuhe werden aus dem Leder junger Ziegen gemacht.");
    expect(book).toContain('alt="Das Weiße Kaninchen mit seinen Handschuhen"');
  });

  test("resolves the picture description's conflict by taking the stage's new wording", async ({ page }) => {
    await page.goto(`${JOB}/text?chapter=chapter`);
    const picture = page.locator("tr", { has: page.getByRole("img", { name: "The White Rabbit with his gloves" }) });
    await expect(picture).toContainText("conflict");
    await picture.getByRole("button", { name: "Resolve conflict" }).click();
    const resolution = page.locator("tr.editor-row");
    // Their wording, the stage's new one, and what changed since they made the edit.
    await expect(resolution).toContainText("Das Weiße Kaninchen mit seinen Handschuhen");
    await expect(resolution).toContainText("Das weiße Kaninchen mit den Handschuhen");
    await resolution.getByRole("radio", { name: "Use the new pipeline text" }).click();
    await resolution.getByPlaceholder("Why you're resolving it this way").fill("The stage's wording is closer");
    await resolution.getByRole("button", { name: "Resolve conflict" }).click();
    await expect(picture).toContainText("Das weiße Kaninchen mit den Handschuhen");
    await expect(picture).not.toContainText("conflict");
    await expect(page.getByRole("complementary").getByRole("button", { name: /The Pool of Tears/ })).not.toContainText("conflict");
  });

  test("rewords the picture's description, reads its history, and reverts it", async ({ page }) => {
    await page.goto(`${JOB}/text?chapter=chapter`);
    const picture = page.locator("tr", { has: page.getByRole("img", { name: "The White Rabbit with his gloves" }) });
    await picture.getByRole("button", { name: "Edit" }).click();
    const editor = page.locator("textarea.editor");
    // A description is one line.
    await editor.fill("Das weiße Kaninchen\nmit den Handschuhen");
    await expect(page.getByText("This is one line: it has no line breaks.")).toBeVisible();
    await editor.fill("Das Weiße Kaninchen, Handschuhe in der Hand");
    await expect(page.getByText("Passes deterministic validation.")).toBeVisible();
    await page.getByPlaceholder("Why you are making this change").fill("As the picture shows him");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(picture).toContainText("edited");

    await picture.getByRole("button", { name: "History" }).click();
    const history = page.locator(".text-history");
    // The edit made before the stage's last run, taking the stage's wording, and this edit.
    await expect(history.locator(".hist-event")).toHaveCount(3);
    await expect(history).toContainText("Says whose gloves they are");
    await expect(history).toContainText("The stage's wording is closer");
    await expect(history).toContainText("As the picture shows him");
    await history.getByPlaceholder("Reason for reverting (required)").fill("The stage had it right");
    await history.getByRole("button", { name: "Revert to pipeline text" }).click();
    await expect(picture).toContainText("Das weiße Kaninchen mit den Handschuhen");
    await expect(picture).not.toContainText("edited");
  });

  test("corrects the book's title and a contents entry, and the book takes both", async ({ page }) => {
    await page.goto(`${JOB}/text`);
    await page.getByRole("complementary").getByRole("button", { name: /Title and contents/ }).click();
    const title = page.getByRole("row", { name: /Alice's Adventures in Wonderland/ });
    await expect(title).toContainText("Book title");
    await expect(title).toContainText("Alice im Wunderland");

    await title.getByRole("button", { name: "Edit" }).click();
    await page.locator("textarea.editor").fill("Alices Abenteuer im Wunderland");
    await page.getByPlaceholder("Why you are making this change").fill("The usual German title");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(title).toContainText("edited");

    const entry = page.getByRole("row", { name: /^\S+ Contents entry Endnotes/ });
    await expect(entry).toContainText("Anmerkungen");
    await entry.getByRole("button", { name: "Edit" }).click();
    await page.locator("textarea.editor").fill("Anmerkungen zum Text");
    await page.getByPlaceholder("Why you are making this change").fill("Says what they are notes to");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(entry).toContainText("edited");

    // Three changes the compiled book does not have yet: these two, and the picture's description
    // back to the stage's wording (the book was compiled with the edit since dropped).
    await recompile(page, 3);
    await expect.poll(() => html(page), { timeout: 60_000 }).toContain("<title>Alices Abenteuer im Wunderland</title>");
    expect(await html(page)).toContain('alt="Das weiße Kaninchen mit den Handschuhen"');
  });
});
