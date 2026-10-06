import { expect, test } from "@playwright/test";

test.describe("A translator opens the dashboard", () => {
  test("sees every book with its languages and where it stands", async ({ page }) => {
    await page.goto("/");
    const german = page.getByRole("row", { name: /^alice-german / });
    await expect(german).toContainText("alice-in-wonderland.epub");
    await expect(german).toContainText("EN → DE");
    await expect(german).toContainText("paused");
    await expect(page.getByRole("row", { name: /^alice-chinese / })).toContainText("EN → ZH");
    await expect(page.getByRole("row", { name: /^alice-german-finished / })).toContainText("complete");
  });

  test("goes straight from the list to the passages that wait for them", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("row", { name: /^alice-german / }).getByRole("link", { name: /Open final review/ }).click();
    await expect(page).toHaveURL(/\/jobs\/alice-german\/review$/);
    await expect(page.getByRole("heading", { name: "Source" })).toBeVisible();
    await expect(page.getByText("3 o'clock").first()).toBeVisible();
  });

  test("can download a finished book from the list", async ({ page }) => {
    await page.goto("/");
    const download = page.waitForEvent("download");
    await page.getByRole("row", { name: /^alice-german-finished / }).getByRole("link", { name: /Download/ }).click();
    expect((await download).suggestedFilename()).toMatch(/\.epub$/);
  });

  test("is not kept waiting on a job they have left", async ({ page }) => {
    await page.goto("/jobs/alice-german/progress");
    await expect(page.getByRole("heading", { name: "Pipeline" })).toBeVisible();
    await page.getByRole("link", { name: "Ollama Translator" }).click();
    await expect(page.getByRole("heading", { name: "Jobs" })).toBeVisible();
    // The job's own page asked the server about it every few seconds; the list must not go on doing so.
    const asked: string[] = [];
    page.on("request", (request) => {
      if (/\/api\/jobs\/alice-german\/(info|progress)/.test(request.url())) asked.push(request.url());
    });
    await page.waitForTimeout(6000);
    expect(asked).toEqual([]);
  });
});
