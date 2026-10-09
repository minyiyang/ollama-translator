// Every catalog must be usable: each message parses as ICU, and a translation
// has the key and the values of the English message it stands in for.

import { IntlMessageFormat } from "intl-messageformat";
import { describe, expect, it } from "vitest";
import en from "./en.json";
import { LOCALES } from "./index";

type Catalog = Record<string, string>;
const translations = import.meta.glob<Catalog>("./locales/*.json", { import: "default", eager: true });
const english: Catalog = en;

type Element = { type: number; value?: string; options?: Record<string, { value: Element[] }>; children?: Element[] };

/**
 * The values a message takes ("count", "name") and the tags it wraps text in ("<link>").
 * A value passed both as given and lower-cased ("label", "labelLower") counts once:
 * a language that keeps the capital on a noun uses the first where English uses the second.
 */
function names(message: string, locale: string): string[] {
  const found = new Set<string>();
  const walk = (elements: Element[]) => {
    for (const element of elements) {
      // 0 is literal text and 7 the `#` of a plural; 8 is a tag; the rest take a value.
      if (element.type === 8) found.add(`<${element.value}>`);
      else if (element.type !== 0 && element.type !== 7 && element.value) found.add(element.value.replace(/Lower$/, ""));
      for (const option of Object.values(element.options ?? {})) walk(option.value);
      walk(element.children ?? []);
    }
  };
  walk(new IntlMessageFormat(message, locale).getAst() as unknown as Element[]);
  return [...found].sort();
}

describe("the English catalog", () => {
  it("has only messages that parse", () => {
    const broken = Object.entries(english).filter(([, message]) => {
      try {
        names(message, "en");
        return false;
      } catch {
        return true;
      }
    });
    expect(broken.map(([key]) => key)).toEqual([]);
  });
});

describe.each(LOCALES.filter((locale) => locale.code !== "en"))("the $code catalog", ({ code }) => {
  const catalog = translations[`./locales/${code}.json`];

  it("exists", () => expect(catalog).toBeTruthy());

  it("has no key English lacks", () => {
    expect(Object.keys(catalog ?? {}).filter((key) => !(key in english))).toEqual([]);
  });

  it("takes the same values as English in every message", () => {
    const wrong: string[] = [];
    for (const [key, message] of Object.entries(catalog ?? {})) {
      if (!(key in english)) continue;
      try {
        const [mine, theirs] = [names(message, code), names(english[key], "en")];
        if (mine.join() !== theirs.join()) wrong.push(`${key}: ${mine.join()} ≠ ${theirs.join()}`);
      } catch (error) {
        wrong.push(`${key}: ${String(error)}`);
      }
    }
    expect(wrong).toEqual([]);
  });
});
