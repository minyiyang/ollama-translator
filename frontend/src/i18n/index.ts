// Interface text: ICU messages looked up by key, in the language the user picked.
// English is bundled and complete; other catalogs load on demand and may be
// partial, falling back to English key by key. See docs/LOCALIZATION.md.

import { IntlMessageFormat } from "intl-messageformat";
import { Children, useSyncExternalStore, type ReactNode } from "react";
import en from "./en.json";

export type MessageKey = keyof typeof en;
type Catalog = Partial<Record<MessageKey, string>>;
export type Values = Record<string, string | number | boolean | Date | null | undefined>;
/** Values for a message with tags: `<b>…</b>` calls `b` with what the tags enclose. */
export type RichValues = Record<string, Values[string] | ReactNode | ((chunks: ReactNode[]) => ReactNode)>;
export type Translate = (key: MessageKey, values?: Values) => string;

/** The interface languages, each named in its own language. */
export const LOCALES = [
  { code: "en", name: "English" },
  { code: "zh-CN", name: "简体中文" },
  { code: "ja", name: "日本語" },
  { code: "fr", name: "Français" },
  { code: "es", name: "Español" },
  { code: "de", name: "Deutsch" },
  { code: "ko", name: "한국어" },
] as const;
export type Locale = (typeof LOCALES)[number]["code"];

const STORAGE_KEY = "ui-language";
const loaders = import.meta.glob<{ default: Catalog }>("./locales/*.json");
const catalogs: Partial<Record<Locale, Catalog>> = { en };
const formatters = new Map<string, IntlMessageFormat>();
const listeners = new Set<() => void>();

const isLocale = (code: string | null): code is Locale => LOCALES.some((locale) => locale.code === code);

function formatter(locale: Locale, key: MessageKey): IntlMessageFormat {
  const own = catalogs[locale]?.[key];
  const from = own === undefined ? "en" : locale;
  let found = formatters.get(`${from}\n${key}`);
  if (!found) {
    // A key the catalog lacks (only possible past the type check) shows as itself.
    found = new IntlMessageFormat(own ?? en[key] ?? key, from);
    formatters.set(`${from}\n${key}`, found);
  }
  return found;
}

function format(locale: Locale, key: MessageKey, values?: RichValues): unknown {
  try {
    return formatter(locale, key).format(values as never);
  } catch {
    // A broken translation must not take the page down: show the English.
    return locale === "en" ? key : format("en", key, values);
  }
}

// One translate function per language, so a hook dependency on it changes with the language.
const translator = (locale: Locale): Translate => (key, values) => String(format(locale, key, values));
let current: { locale: Locale; t: Translate } = { locale: "en", t: translator("en") };

function apply(locale: Locale) {
  current = { locale, t: translator(locale) };
  document.documentElement.lang = locale;
  listeners.forEach((notify) => notify());
}

const subscribe = (notify: () => void) => {
  listeners.add(notify);
  return () => void listeners.delete(notify);
};

/** The interface language in use. */
export const getLocale = (): Locale => current.locale;

/** Translate outside a component. Inside one, use `useT` so it re-renders with the language. */
export const t: Translate = (key, values) => current.t(key, values);

/** A message with tags or elements among its values, as React content. */
export function rich(key: MessageKey, values: RichValues): ReactNode {
  const parts = format(current.locale, key, values);
  return Array.isArray(parts) ? Children.toArray(parts) : (parts as ReactNode);
}

/** The translate function; the component re-renders when the language changes. */
export function useT(): Translate {
  return useSyncExternalStore(subscribe, () => current.t);
}

export function useLocale(): Locale {
  return useSyncExternalStore(subscribe, getLocale);
}

/** Switch the interface language, loading its catalog first, and remember the choice. */
export async function setLocale(locale: Locale, remember = true): Promise<void> {
  if (!catalogs[locale]) {
    const load = loaders[`./locales/${locale}.json`];
    catalogs[locale] = load ? (await load()).default : {};
  }
  if (remember) {
    try {
      window.localStorage.setItem(STORAGE_KEY, locale);
    } catch {
      // Storage is blocked: the choice lasts for this page only.
    }
  }
  apply(locale);
}

/** Start in the saved language, or English. Never rejects: a catalog that fails to load leaves English. */
export async function initLocale(): Promise<void> {
  let saved: string | null = null;
  try {
    saved = window.localStorage.getItem(STORAGE_KEY);
  } catch {
    // Storage is blocked.
  }
  if (isLocale(saved) && saved !== "en") await setLocale(saved, false).catch(() => undefined);
}

/** Back to English, for tests. */
export const resetLocale = () => apply("en");
