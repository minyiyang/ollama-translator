import { expect, test } from "@playwright/test";
import { fileURLToPath } from "node:url";

// e2e/serve.py writes this book when it starts.
const BOOK = fileURLToPath(new URL("./.books/the-sign-of-the-four.epub", import.meta.url));

test.describe("A translator changes the interface language", () => {
  test("sees the dashboard in that language at once, and still after a reload", async ({ page }) => {
    await page.goto("/");
    await expect(page.getByRole("link", { name: "Jobs", exact: true })).toBeVisible();

    await page.getByRole("combobox", { name: "Interface language" }).selectOption({ label: "简体中文" });
    await expect(page.getByRole("link", { name: "任务", exact: true })).toBeVisible();
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
    // The books themselves are not interface text.
    await expect(page.getByRole("row", { name: /^alice-german / })).toContainText("EN → DE");

    await page.reload();
    await expect(page.getByRole("link", { name: "任务", exact: true })).toBeVisible();
    await expect(page.getByRole("combobox", { name: "界面语言" })).toHaveValue("zh-CN");
  });

  test("keeps the language from one page of a job to the next", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("combobox", { name: "Interface language" }).selectOption({ label: "Français" });
    await expect(page.locator("html")).toHaveAttribute("lang", "fr");
    await page.goto("/jobs/alice-german/progress");
    await expect(page.getByRole("link", { name: "Progression", exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Langue de l’interface" }).selectOption({ label: "English" });
    await expect(page.getByRole("link", { name: "Progress", exact: true })).toBeVisible();
  });
});

test.describe("A translator reads what the pipeline says in their language", () => {
  test("sees a stage's description and its status message translated", async ({ page }) => {
    await page.goto("/");
    await page.getByRole("combobox", { name: "Interface language" }).selectOption({ label: "简体中文" });
    await expect(page.locator("html")).toHaveAttribute("lang", "zh-CN");
    await page.goto("/jobs/alice-german/progress");

    // The stage wrote "1 segment(s) require human review"; the dashboard says it in Chinese.
    await expect(page.getByText(/\d+ 个片段需要人工复核/).first()).toBeVisible();
    await expect(page.getByText(/segment\(s\) require human review/)).toHaveCount(0);

    await page.getByRole("button", { name: "关于此阶段" }).first().hover();
    await expect(page.getByRole("tooltip")).toContainText("将 EPUB 解包");
  });

  test("is told why the server refused, in the interface language", async ({ page }) => {
    // Their list is out of date: it does not know a job the server has, so only the server can refuse the name.
    await page.route("**/api/setup", async (route) => {
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ response, json: { ...body, jobs: body.jobs.filter((job: { job_id: string }) => job.job_id !== "alice-german") } });
    });
    await page.goto("/");
    await page.getByRole("combobox", { name: "Interface language" }).selectOption({ label: "Deutsch" });
    await page.getByRole("button", { name: "+ Neuen Job anlegen" }).click();
    const dialog = page.getByRole("dialog", { name: "Neuer Übersetzungsjob" });
    await dialog.locator("input[type=file]").setInputFiles(BOOK);
    await dialog.getByRole("textbox", { name: "Name der Konfigurationsdatei" }).fill("alice-german.yaml");
    await dialog.getByRole("button", { name: "Job anlegen" }).click();
    await expect(page.getByText("Ein Job namens alice-german existiert bereits")).toBeVisible();
  });
});
