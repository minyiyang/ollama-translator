import { expect, test, type Page } from "@playwright/test";

// The layout-test languages (src/i18n/pseudo.ts) are chosen as a saved interface language.
const PAGES = ["/", "/series", "/jobs/alice-german/config", "/jobs/alice-german/glossary", "/jobs/alice-german/progress",
  "/jobs/alice-german/text", "/jobs/alice-german/review"];

async function open(page: Page, language: string, path: string) {
  await page.addInitScript((code) => window.localStorage.setItem("ui-language", code), language);
  await page.goto(path);
  await page.waitForLoadState("networkidle");
}

/** How far the page is wider than the window: text or a panel sticking out. */
const overflow = (page: Page) => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);

test.describe("The dashboard in a language written right to left", () => {
  for (const path of PAGES) {
    test(`fits the window on ${path}`, async ({ page }) => {
      await open(page, "ar-XB", path);
      await expect(page.locator("html")).toHaveAttribute("dir", "rtl");
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  }

  test("starts from the right: the header, the sidebar, and a table's first column", async ({ page }) => {
    await open(page, "ar-XB", "/jobs/alice-german/text");
    const left = async (selector: string) => (await page.locator(selector).first().boundingBox())!.x;
    expect(await left("header.shell .brand")).toBeGreaterThan(await left("header.shell nav.tabs"));
    expect(await left(".sidenav")).toBeGreaterThan(await left(".side-layout > main"));

    await page.goto("/");
    const cells = page.getByRole("row", { name: /alice-german / }).first().getByRole("cell");
    expect((await cells.first().boundingBox())!.x).toBeGreaterThan((await cells.nth(1).boundingBox())!.x);
  });

  test("keeps identifiers and the book's own text left to right", async ({ page }) => {
    await open(page, "ar-XB", "/jobs/alice-german/text");
    const directionOf = (selector: string) => page.locator(selector).first().evaluate((element) => getComputedStyle(element).direction);
    expect(await directionOf("td.mono")).toBe("ltr");
    expect(await directionOf("td[lang=en]")).toBe("ltr");
    expect(await directionOf("table.grid th")).toBe("rtl");
  });
});

test.describe("The dashboard in a language with longer words", () => {
  for (const path of PAGES) {
    test(`fits the window on ${path}`, async ({ page }) => {
      await open(page, "en-XA", path);
      expect(await overflow(page)).toBeLessThanOrEqual(0);
    });
  }
});
