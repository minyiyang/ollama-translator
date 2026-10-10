import { expect, test } from "@playwright/test";

// A started job's config is unlocked, changed, and saved with a rerun of the stage the change first affects.
test.describe("A translator changes the config of a book that is already translated", () => {
  test("is shown the config locked, and what a change would do before anything is saved", async ({ page }) => {
    await page.goto("/jobs/alice-german-reconfigure/config");
    await expect(page.getByText(/Unlock it to change a setting/)).toBeVisible();
    const format = page.locator(".opt", { has: page.getByText("output.format", { exact: true }) }).getByRole("combobox");
    await expect(format).toBeDisabled();

    await page.getByRole("button", { name: "Unlock to edit" }).click();
    await expect(page.getByText("You are editing this job's configuration. Nothing changes until you save.")).toBeVisible();
    const changes = page.locator(".card", { has: page.getByRole("heading", { name: "Changes" }) });
    await expect(changes.getByText("No changes yet.")).toBeVisible();
    // The pair of languages stays locked, with the reason.
    await expect(page.getByRole("combobox", { name: "Into language" })).toBeDisabled();
    await expect(page.getByText("Locked once a job has started: another pair of languages is another job.").first()).toBeVisible();

    // A setting nothing already done depends on: saved without a rerun.
    await page.getByRole("tab", { name: "All settings" }).click();
    const timeout = page.locator(".opt", { has: page.getByText("ollama.timeout_seconds", { exact: true }) }).getByRole("spinbutton");
    await timeout.fill("900");
    await expect(changes.getByText("Applies to whatever runs next; nothing already done depends on it.")).toBeVisible();
    await expect(changes.getByText("No finished stage read these settings, so nothing has to be rerun.")).toBeVisible();
    await changes.getByRole("button", { name: "Discard changes" }).click();
    await expect(page.getByRole("button", { name: "Unlock to edit" })).toBeVisible();
  });

  test("changes the output format, reruns from the build, and gets the book in the new format", async ({ page }) => {
    await page.goto("/jobs/alice-german-reconfigure/config");
    await page.getByRole("button", { name: "Unlock to edit" }).click();
    const format = page.locator(".opt", { has: page.getByText("output.format", { exact: true }) }).getByRole("combobox");
    await format.selectOption("docx");

    const changes = page.locator(".card", { has: page.getByRole("heading", { name: "Changes" }) });
    await expect(changes.getByText("Output format", { exact: true })).toBeVisible();
    await expect(changes.getByText("First read by Build the book, which has finished. Only the output is rebuilt: seconds, no model call.")).toBeVisible();
    await changes.getByRole("button", { name: "Save and rerun from Build the book" }).click();

    // The rerun dialog of the Progress tab: the two stages redone, and what is replaced.
    const dialog = page.getByRole("alertdialog");
    await expect(dialog.getByRole("heading", { name: "Rerun from Build the book?" })).toBeVisible();
    await expect(dialog.getByText("Check the book")).toBeVisible();
    await expect(dialog.getByText("The compiled output is replaced by the new one.")).toBeVisible();
    await dialog.getByRole("button", { name: /Rerun/ }).click();

    await expect(page).toHaveURL(/\/jobs\/alice-german-reconfigure\/progress$/);
    const banner = page.getByRole("banner");
    await expect(banner.getByText("complete", { exact: true })).toBeVisible({ timeout: 60_000 });
    // The job now gives back a Word document, and says so where it is downloaded.
    await expect(banner.getByRole("combobox", { name: "Download format" })).toHaveValue("docx");
    const download = page.waitForEvent("download");
    await banner.getByRole("link", { name: /Download/ }).click();
    expect((await download).suggestedFilename()).toMatch(/\.docx$/);

    // The change is on record with the job.
    await page.goto("/jobs/alice-german-reconfigure/config");
    await expect(page.getByText("1 earlier change to this configuration")).toBeVisible();
    await expect(page.locator(".opt", { has: page.getByText("output.format", { exact: true }) }).getByRole("combobox")).toHaveValue("docx");
  });
});
