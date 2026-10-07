import { describe, expect, it } from "vitest";
import { cueLabel, readingProblems, type Cue } from "./subtitles";

const LIMITS = { line_characters: 42, lines: 2, characters_per_second: 20 };
const cue = (over: Partial<Cue> = {}): Cue => ({ number: 8, start: "0:00:27", seconds: 1.2, speakers: false, other_characters: 0, ...over });

describe("a subtitle cue in the dashboard", () => {
  it("is labelled with when it comes on screen and for how long", () => {
    expect(cueLabel(cue())).toBe("0:00:27 · 1.2 s");
    expect(cueLabel(cue({ start: "1:02:03", seconds: 4 }))).toBe("1:02:03 · 4.0 s");
  });

  it("is fine when it can be read in its time and fits its two lines", () => {
    expect(readingProblems("Mein Geist rebelliert.", cue(), LIMITS)).toEqual([]);
  });

  it("is too long when it says more than can be read while it is on screen", () => {
    expect(readingProblems("Mein Geist rebelliert gegen den Stillstand.", cue(), LIMITS)).toEqual([
      "too long to read in 1.2 s: 38 characters, about 24 can be read",
    ]);
  });

  it("does not fit when it needs more than its lines hold, however long it is shown", () => {
    const text = "Wenn du nicht anständig sein kannst, solltest du die Geschichte lieber selbst zu Ende erzählen.";
    expect(readingProblems(text, cue({ seconds: 9 }), LIMITS)).toEqual(["does not fit 2 lines of 42: 95 characters"]);
  });

  it("counts both speakers of a cue for its time, and each speaker's line with its dash for its room", () => {
    const first = cue({ seconds: 2.8, speakers: true, other_characters: 15 });
    // "Einen, in der Tat!" is the other speaker: 15 characters to read as well.
    expect(readingProblems("Ich wage zu sagen, dass es vielleicht einen gibt.", first, LIMITS)).toEqual([
      "line too long: 51 characters, 42 fit",
    ]);
    expect(readingProblems("Ich vermute, es gibt einen.", cue({ seconds: 1.5, speakers: true, other_characters: 15 }), LIMITS)).toEqual([
      "too long to read in 1.5 s: 38 characters, about 30 can be read",
    ]);
  });

  it("says nothing about a book's passage, which has no cue", () => {
    expect(readingProblems("A very long paragraph of a novel. ".repeat(10), undefined, null)).toEqual([]);
    expect(readingProblems("text", cue(), undefined)).toEqual([]);
  });
});
