import { expect, test } from "@playwright/test";

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
    await page.goto("/jobs/alice-german/progress");
    await expect(page.getByRole("link", { name: "Progression", exact: true })).toBeVisible();
    await page.getByRole("combobox", { name: "Langue de l’interface" }).selectOption({ label: "English" });
    await expect(page.getByRole("link", { name: "Progress", exact: true })).toBeVisible();
  });
});
