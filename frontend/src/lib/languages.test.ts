import { describe, expect, it } from "vitest";
import { apiError, mockApi } from "../test/mockApi";
import {
  FALLBACK_PAIR, SKIP_HIDES, TIER_HELP, hiddenOptions, isGeneric, langAttr, leftoverSourceText, loadLanguages, pairCodes, pairOf, scriptsOf,
  type Language, type LanguageSupport,
} from "./languages";

const lang = (code: string, tier: Language["tier"]): Language => ({ code, name: code.toUpperCase(), tier });
const support = (source: Language["tier"], target: Language["tier"], skipped: LanguageSupport["skipped"] = []): LanguageSupport => ({
  pair: "en>ja", source: lang("en", source), target: lang("ja", target), skipped, notice: "",
});

describe("pair spelling", () => {
  it("reads incomplete and malformed pairs as empty sides instead of throwing", () => {
    expect(pairCodes("")).toEqual(["", ""]);
    expect(pairCodes("en")).toEqual(["en", ""]);
    expect(pairCodes("en>")).toEqual(["en", ""]);
    expect(pairCodes("en-ja")).toEqual(["en-ja", ""]); // only en-zh and zh-en use the hyphen
  });

  it("trims the codes it joins", () => {
    expect(pairOf(" en ", " zh ")).toBe("en-zh");
    expect(pairOf(" pt-BR", "fr ")).toBe("pt-BR>fr");
  });

  it("round-trips every spelling it writes", () => {
    for (const [source, target] of [["en", "zh"], ["zh", "en"], ["en", "ja"], ["pt-BR", "zh"], ["zh", "zh-Hant"]]) {
      expect(pairCodes(pairOf(source, target))).toEqual([source, target]);
    }
  });

  it("falls back to an English–Chinese glossary pair", () => {
    expect(pairCodes(FALLBACK_PAIR.pair)).toEqual(["en", "zh"]);
  });
});

describe("langAttr", () => {
  it("writes zh as Simplified Chinese and passes other codes through", () => {
    expect(langAttr("zh")).toBe("zh-CN");
    expect(langAttr("zh-Hant")).toBe("zh-Hant");
    expect(langAttr("en")).toBe("en");
    expect(langAttr("")).toBe("");
  });
});

describe("isGeneric", () => {
  it("is true as soon as either side is not tuned", () => {
    expect(isGeneric(support("tuned", "tuned"))).toBe(false);
    expect(isGeneric(support("tuned", "generic"))).toBe(true);
    expect(isGeneric(support("profiled", "tuned"))).toBe(true);
  });

  it("is false while the support is unknown", () => {
    expect(isGeneric(null)).toBe(false);
    expect(isGeneric(undefined)).toBe(false);
  });

  it("has help text for every tier", () => {
    for (const tier of ["tuned", "profiled", "generic"] as const) expect(TIER_HELP[tier].length).toBeGreaterThan(10);
  });
});

describe("hiddenOptions", () => {
  it("maps each hidden option to the skipped check that hides it", () => {
    const conventions = { check: "punctuation conventions", reason: "no house conventions for Japanese" };
    const reprose = { check: "prose rewrite", reason: "written for Chinese" };
    const hidden = hiddenOptions(support("tuned", "generic", [reprose, conventions]));
    expect([...hidden.keys()].sort()).toEqual([...SKIP_HIDES["prose rewrite"], ...SKIP_HIDES["punctuation conventions"]].sort());
    expect(hidden.get("consistency.conventions")).toBe(conventions);
    expect(hidden.get("reprose.model")).toBe(reprose);
  });

  it("hides nothing for a check with no options, or without support", () => {
    expect(hiddenOptions(support("tuned", "generic", [{ check: "length ratio", reason: "no profile" }])).size).toBe(0);
    expect(hiddenOptions(null).size).toBe(0);
    expect(hiddenOptions(undefined).size).toBe(0);
  });
});

describe("loadLanguages", () => {
  const payload = (pair: string) => ({ languages: [lang("en", "tuned")], support: null, error: pair ? "" : "not a pair" });

  it("asks the server about the pair, escaping it", async () => {
    const api = mockApi({ "GET /api/languages": (_: unknown, url: URL) => payload(url.searchParams.get("pair") ?? "") });
    await expect(loadLanguages("pt-BR>ja")).resolves.toEqual(payload("pt-BR>ja"));
    expect(api.requested("/api/languages?pair=pt-BR%3Eja")).toBe(true);
  });

  it("asks once per pair, however many callers want it", async () => {
    const api = mockApi({ "GET /api/languages": (_: unknown, url: URL) => payload(url.searchParams.get("pair") ?? "") });
    const [first, second] = await Promise.all([loadLanguages("en>ko"), loadLanguages("en>ko")]);
    expect(second).toBe(first);
    await loadLanguages("en>ko");
    expect(api.count("/api/languages?pair=en%3Eko")).toBe(1);

    await loadLanguages("en>de");
    expect(api.count("/api/languages?pair=en%3Ede")).toBe(1);
  });

  it("lists the languages when called without a pair", async () => {
    const api = mockApi({ "GET /api/languages": payload("") });
    await expect(loadLanguages()).resolves.toEqual(payload(""));
    expect(api.requested("/api/languages?pair=")).toBe(true);
  });

  it("does not cache a failure: the next call asks again", async () => {
    mockApi({ "GET /api/languages": apiError("server restarting", 503) });
    await expect(loadLanguages("en>fr")).rejects.toThrow("server restarting");

    const api = mockApi({ "GET /api/languages": payload("en>fr") });
    await expect(loadLanguages("en>fr")).resolves.toEqual(payload("en>fr"));
    expect(api.count("/api/languages?pair=en%3Efr")).toBe(1);
  });
});

describe("scriptsOf", () => {
  it("names the scripts a language is written in", () => {
    expect(scriptsOf("en")).toEqual(["Latn"]);
    expect(scriptsOf("pt-BR")).toEqual(["Latn"]);
    expect(scriptsOf("ru")).toEqual(["Cyrl"]);
    expect(scriptsOf("zh")).toEqual(["Hani"]);
    expect(scriptsOf("zh-Hant")).toEqual(["Hani"]);
    expect(scriptsOf("ja")).toEqual(["Hani", "Hira", "Kana"]);
    expect(scriptsOf("ko")).toEqual(["Hang", "Hani"]);
  });

  it("knows nothing about a code that is not a language", () => {
    expect(scriptsOf("")).toEqual([]);
    expect(scriptsOf("not a code")).toEqual([]);
    expect(scriptsOf("xx")).toEqual([]);
  });
});

describe("leftoverSourceText", () => {
  it("finds source-script words left in a translation into another script", () => {
    expect(leftoverSourceText("爱丽丝跟着 White Rabbit 跳进了 rabbit hole。", "en", "zh")).toEqual(["White", "Rabbit", "rabbit", "hole"]);
    expect(leftoverSourceText("Ah Q lived in the 土谷祠 at 未庄.", "zh", "en")).toEqual(["土谷祠", "未庄"]);
    expect(leftoverSourceText("Алиса увидела the White Rabbit.", "en", "ru")).toEqual(["the", "White", "Rabbit"]);
  });

  it("lists a repeated word once and ignores a single stray letter", () => {
    expect(leftoverSourceText("阿Q 说 Rabbit，又说 Rabbit。", "en", "zh")).toEqual(["Rabbit"]); // the Q of 阿Q is a name, not English
  });

  it("flags nothing for two languages written in the same script", () => {
    expect(leftoverSourceText("Alice fing an, sich zu langweilen.", "en", "de")).toEqual([]);
    expect(leftoverSourceText("Longtemps, je me suis couché.", "fr", "en")).toEqual([]);
    expect(leftoverSourceText("愛麗絲開始覺得無聊。", "zh", "zh-Hant")).toEqual([]);
  });

  it("flags only the script the target does not use at all", () => {
    expect(leftoverSourceText("阿Qには家がなかった。", "zh", "ja")).toEqual([]); // Japanese is written with Han too
    expect(leftoverSourceText("我是猫。まだ名字没有。", "ja", "zh")).toEqual(["まだ"]);
  });

  it("flags nothing when either language is unknown", () => {
    expect(leftoverSourceText("White Rabbit", "en", "xx")).toEqual([]);
    expect(leftoverSourceText("White Rabbit", "", "zh")).toEqual([]);
  });
});
