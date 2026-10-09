// Languages nobody speaks, made from the English messages to test the layout:
// one stretches and accents the text, the other writes it in Arabic letters so
// the page runs right to left. Text that stays plain was never in the catalog.
// See docs/LOCALIZATION.md, 2.6.

import { IntlMessageFormat } from "intl-messageformat";

type Ast = ReturnType<IntlMessageFormat["getAst"]>;

/** Chosen by saving the code as the interface language; the menu lists them in development only. */
export const PSEUDO_LOCALES = [
  { code: "en-XA", name: "[Ƥşḗŭḓǿ ~~~]" },
  { code: "ar-XB", name: "[پسعؤدو ~~~]" },
] as const;
export type PseudoLocale = (typeof PSEUDO_LOCALES)[number]["code"];

export const isPseudo = (code: string | null): code is PseudoLocale => PSEUDO_LOCALES.some((locale) => locale.code === code);

const LATIN = "abcdefghijklmnopqrstuvwxyz";
const ACCENTED = "àƀçḓḗƒğħîĵķļḿñöƥɋŕšţŭṽŵẋýž";
const ARABIC = "ابكدعفغهيجقلمنوپقرستؤڤشخىز";
// A real right-to-left catalog points its arrows the other way.
const TURNED: Record<string, string> = { "→": "←", "←": "→" };

const letters = (to: string) => (text: string) =>
  [...text].map((letter) => TURNED[to === ARABIC ? letter : ""] ?? to[LATIN.indexOf(letter.toLowerCase())] ?? letter).join("");

/** The message with its own text rewritten; values, tags and plural forms stay as they are. */
function rewrite(elements: Ast, text: (literal: string) => string): Ast {
  return elements.map((element) => {
    if (element.type === 0) return { ...element, value: text(element.value) };
    if (element.type === 5 || element.type === 6) {
      const options = Object.entries(element.options).map(([name, option]) => [name, { ...option, value: rewrite(option.value, text) }]);
      return { ...element, options: Object.fromEntries(options) };
    }
    if (element.type === 8) return { ...element, children: rewrite(element.children, text) };
    return element;
  }) as Ast;
}

/** An English message in a pseudo-language, about a third longer, as translations tend to be. */
export function pseudoMessage(english: string, locale: PseudoLocale): IntlMessageFormat {
  const ast = rewrite(new IntlMessageFormat(english, "en").getAst(), letters(locale === "ar-XB" ? ARABIC : ACCENTED));
  const padding = { type: 0, value: ` ${"~".repeat(Math.ceil(english.length / 3))}` };
  // Plural forms are chosen as in English: the text is English underneath.
  return new IntlMessageFormat([...ast, padding] as Ast, "en");
}
