import { expect, test, type Page } from "@playwright/test";

// alice-german-approval is the annotated chapter of Alice, translated without a fault: nothing
// is queued for its reviewer. Its config compiles only a draft a person has approved, so the
// run stopped before the compile, and the approval is given on its own, with no decision to
// apply it with.
//
// The tests take the job from there one after another, as one reviewer would.
test.describe.configure({ mode: "serial" });

const JOB = "/jobs/alice-german-approval";
const html = async (page: Page) => (await page.request.get(`/api${JOB}/output?format=html`)).text();

async function approveAndCompile(page: Page) {
  await expect(page.getByText("The final draft is waiting for approval. No passage is waiting for a decision.")).toBeVisible();
  await page.getByRole("region", { name: "Approve final draft" }).getByRole("button", { name: "Approve final draft" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Final draft approved." })).toBeVisible();
  await expect(page.getByRole("region", { name: "Approve final draft" })).toHaveCount(0);
  await page.getByRole("button", { name: "Compile now" }).click();
  await expect(page.getByRole("status").filter({ hasText: "Compile finished: complete" })).toBeVisible({ timeout: 60_000 });
}

test.describe("A reviewer approves a draft that has nothing queued for a decision", () => {
  test("approves the draft the run stopped at, and compiles the book", async ({ page }) => {
    await page.goto(`${JOB}/review`);
    await expect(page.getByRole("button", { name: "Apply decisions" })).toHaveCount(0); // no queue to apply
    await approveAndCompile(page);
    await expect(page.getByRole("banner")).toContainText("complete");
    expect(await html(page)).toContain("<title>Alice im Wunderland</title>");
  });

  test("corrects the book's title afterwards, is asked to approve the draft again, and gets the book with it", async ({ page }) => {
    await page.goto(`${JOB}/text`);
    await page.getByRole("complementary").getByRole("button", { name: /Title and contents/ }).click();
    const title = page.getByRole("row", { name: /Alice's Adventures in Wonderland/ });
    await title.getByRole("button", { name: "Edit" }).click();
    await page.locator("textarea.editor").fill("Alices Abenteuer im Wunderland");
    await page.getByPlaceholder("Why you are making this change").fill("The usual German title");
    await page.getByRole("button", { name: "Save", exact: true }).click();
    await expect(title).toContainText("edited");

    // What was approved had the other title: the recompile stops for a person's approval.
    await expect(page.getByText("1 edit not in the compiled book.")).toBeVisible();
    await page.getByRole("button", { name: "Recompile" }).click();
    await page.getByRole("alertdialog").getByRole("button", { name: "Recompile" }).click();
    await expect.poll(async () => (await page.request.get(`/api${JOB}/review`)).json().then((review) => review.status.overall), { timeout: 60_000 }).toBe("paused");

    // The queue is as empty as it was: the approval is given on its own again.
    await page.goto(`${JOB}/review`);
    await approveAndCompile(page);
    expect(await html(page)).toContain("<title>Alices Abenteuer im Wunderland</title>");
  });
});
