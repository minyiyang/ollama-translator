// Hand the untranslated interface messages to a translator and take them back.
//
//   npm run i18n -- status                    coverage of every language
//   npm run i18n -- md                        write i18n-work/strings.md: every message some language lacks
//   npm run i18n -- merge-md <locale> <file>  check a translated strings.md and merge it into the catalog
//   npm run i18n -- missing <locale>          write i18n-work/<locale>.todo.json: the English still to translate
//   npm run i18n -- merge <locale> [file]     check a translated JSON file and merge it into the catalog
//
// strings.md has one numbered section per message. Translate the text under each
// heading; the number in the heading is what ties a section to its message, so
// the headings may be reworded but not renumbered or reordered.
//
// A todo file is flat JSON, key -> English message. Translate the values, keep
// the keys, and merge the file back (by default i18n-work/<locale>.done.json).
//
// Messages are ICU: keep each {value}, <tag>, and plural or select keyword as it is.

import { existsSync, mkdirSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { IntlMessageFormat } from "intl-messageformat";

const ROOT = join(dirname(fileURLToPath(import.meta.url)), "..");
const CATALOGS = join(ROOT, "src", "i18n");
const WORK = join(ROOT, "i18n-work");

const read = (path) => JSON.parse(readFileSync(path, "utf-8"));
const write = (path, value) => writeFileSync(path, `${JSON.stringify(value, null, 2)}\n`, "utf-8");
const english = read(join(CATALOGS, "en.json"));
const localePath = (locale) => join(CATALOGS, "locales", `${locale}.json`);
const locales = () => readdirSync(join(CATALOGS, "locales")).filter((name) => name.endsWith(".json")).map((name) => name.slice(0, -5));

/** The values and tags a message takes; "label" and "labelLower" are one value (see catalog.test.ts). */
function names(message, locale) {
  const found = new Set();
  const walk = (elements) => {
    for (const element of elements) {
      if (element.type === 8) found.add(`<${element.value}>`);
      else if (element.type !== 0 && element.type !== 7 && element.value) found.add(element.value.replace(/Lower$/, ""));
      for (const option of Object.values(element.options ?? {})) walk(option.value);
      walk(element.children ?? []);
    }
  };
  walk(new IntlMessageFormat(message, locale).getAst());
  return [...found].sort().join(", ");
}

function catalogOf(locale) {
  if (!existsSync(localePath(locale))) throw new Error(`no catalog for ${locale}; the languages are ${locales().join(", ")}`);
  return read(localePath(locale));
}

function status() {
  const total = Object.keys(english).length;
  for (const locale of locales()) {
    const have = Object.keys(catalogOf(locale)).filter((key) => key in english).length;
    console.log(`${locale.padEnd(6)} ${String(have).padStart(5)} / ${total}  ${total - have ? `${total - have} missing` : "complete"}`);
  }
}

function missing(locale) {
  const catalog = catalogOf(locale);
  const todo = Object.fromEntries(Object.entries(english).filter(([key]) => !(key in catalog)));
  mkdirSync(WORK, { recursive: true });
  const path = join(WORK, `${locale}.todo.json`);
  write(path, todo);
  console.log(`${Object.keys(todo).length} messages to translate: ${path}`);
}

function markdown() {
  const lacking = new Set(locales().flatMap((locale) => Object.keys(english).filter((key) => !(key in catalogOf(locale)))));
  const keys = Object.keys(english).filter((key) => lacking.has(key));
  mkdirSync(WORK, { recursive: true });
  // The numbers in strings.md stand for these keys, whatever the English catalog becomes later.
  write(join(WORK, "strings.keys.json"), keys);
  const sections = keys.map((key, index) => `## ${index + 1} · \`${key}\`\n\n${english[key]}\n`);
  const path = join(WORK, "strings.md");
  writeFileSync(path, `# Interface messages to translate\n\n${sections.join("\n")}`, "utf-8");
  console.log(`${keys.length} messages: ${path}`);
}

/** A translated strings.md as key -> message, by the number in each heading. */
function fromMarkdown(file) {
  const keys = read(join(WORK, "strings.keys.json"));
  const translated = {};
  const sections = readFileSync(file, "utf-8").replace(/\r\n/g, "\n").split(/^##[ \t]+(?=\d)/m).slice(1);
  for (const section of sections) {
    const [heading, ...body] = section.split("\n");
    const number = Number.parseInt(heading, 10);
    const key = keys[number - 1];
    if (!key) throw new Error(`section ${number} is not one of the ${keys.length} messages`);
    if (key in translated) throw new Error(`section ${number} appears twice`);
    translated[key] = body.join(" ").replace(/\s+/g, " ").trim();
  }
  const absent = keys.map((_, index) => index + 1).filter((number) => !(keys[number - 1] in translated));
  if (absent.length) console.warn(`sections not found, left untranslated: ${absent.join(", ")}`);
  return translated;
}

function merge(locale, file = join(WORK, `${locale}.done.json`), translated = read(file)) {
  const catalog = catalogOf(locale);
  const problems = [];
  let same = 0;
  for (const [key, message] of Object.entries(translated)) {
    if (!(key in english)) problems.push(`${key}: not a key of the English catalog`);
    else if (typeof message !== "string" || !message.trim()) problems.push(`${key}: empty`);
    else {
      try {
        const [mine, theirs] = [names(message, locale), names(english[key], "en")];
        if (mine !== theirs) problems.push(`${key}: takes {${mine}} where English takes {${theirs}}`);
      } catch (error) {
        problems.push(`${key}: ${error.message}`);
      }
      if (message === english[key]) same += 1;
    }
  }
  if (problems.length) {
    console.error(`${problems.length} problem(s); nothing was merged:\n  ${problems.join("\n  ")}`);
    process.exit(1);
  }
  // The catalog keeps the English catalog's order.
  const merged = { ...catalog, ...translated };
  write(localePath(locale), Object.fromEntries(Object.keys(english).filter((key) => key in merged).map((key) => [key, merged[key]])));
  const left = Object.keys(english).filter((key) => !(key in merged)).length;
  console.log(`merged ${Object.keys(translated).length} messages into ${locale} (${same} identical to English); ${left} still missing`);
}

const [command, locale, file] = process.argv.slice(2);
if (command === "status") status();
else if (command === "md") markdown();
else if (command === "merge-md" && locale && file) merge(locale, file, fromMarkdown(file));
else if (command === "missing" && locale) missing(locale);
else if (command === "merge" && locale) merge(locale, file);
else {
  console.error("usage: npm run i18n -- status | md | merge-md <locale> <file> | missing <locale> | merge <locale> [file]");
  process.exit(2);
}
