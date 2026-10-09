import { describe, expect, it } from "vitest";
import { setLocale } from "../i18n";
import { rerunWarning, serverError, stageMessage } from "./serverText";

describe("serverError", () => {
  it("says a coded error in the interface language, with its values", async () => {
    const body = { error: "a job named a1 already exists", code: "job_exists", params: { job: "a1" } };
    expect(serverError(body)).toBe("a job named a1 already exists");
    await setLocale("zh-CN");
    expect(serverError(body)).toMatch(/a1/);
    expect(serverError(body)).not.toBe(body.error);
  });

  it("shows the server's text for a code it does not know, or none", () => {
    expect(serverError({ error: "boom", code: "no_such_code" })).toBe("boom");
    expect(serverError({ error: "boom" })).toBe("boom");
  });

  it("falls back to what the caller gives when the body says nothing", () => {
    expect(serverError({}, "Bad Gateway")).toBe("Bad Gateway");
    expect(serverError(null, "Bad Gateway")).toBe("Bad Gateway");
  });
});

describe("stageMessage", () => {
  it("recognizes a stage's own message and its count", async () => {
    expect(stageMessage("3 segment(s) require human review")).toBe("3 segment(s) require human review");
    await setLocale("de");
    expect(stageMessage("3 segment(s) require human review")).toMatch(/3/);
    expect(stageMessage("3 segment(s) require human review")).not.toContain("require human review");
    expect(stageMessage("paused on request; resume to continue")).not.toContain("paused on request");
  });

  it("leaves any other message as the stage wrote it", async () => {
    await setLocale("de");
    expect(stageMessage("configured glossary reuse; no LLM extraction")).toBe("configured glossary reuse; no LLM extraction");
    expect(stageMessage("x 3 segment(s) require human review")).toBe("x 3 segment(s) require human review");
  });
});

describe("rerunWarning", () => {
  it("uses the catalog for a known code and the server's text otherwise", () => {
    expect(rerunWarning({ code: "compiled_epub", message: "ignored" })).toBe("The compiled output is replaced by the new one.");
    expect(rerunWarning({ code: "new_code", message: "Something new." })).toBe("Something new.");
  });
});
