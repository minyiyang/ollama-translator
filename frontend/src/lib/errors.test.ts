import { describe, expect, it } from "vitest";
import { describeError } from "./errors";

describe("describeError", () => {
  it("uses an Error's message and stack", () => {
    const error = new TypeError("boom");
    expect(describeError(error)).toEqual({ message: "boom", details: error.stack });
  });

  it("falls back to the error name when the message is empty", () => {
    expect(describeError(new RangeError("")).message).toBe("RangeError");
  });

  it("describes thrown non-Error values", () => {
    expect(describeError("plain text")).toEqual({ message: "plain text", details: "" });
    expect(describeError({ code: 7 })).toEqual({ message: '{"code":7}', details: "" });
    expect(describeError(undefined).message).toBe("undefined");
  });

  it("survives values JSON cannot serialize", () => {
    const circular: Record<string, unknown> = {};
    circular.self = circular;
    expect(describeError(circular).message).toBe("[object Object]");
  });
});
