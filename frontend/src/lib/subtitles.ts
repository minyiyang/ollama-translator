import { t } from "../i18n";

/** A subtitle job's limits: what fits on the screen and can be read in the time. */
export type ReadingLimits = { line_characters: number; lines: number; characters_per_second: number };

/** The cue a passage of a subtitle job belongs to. */
export type Cue = {
  number: number;
  /** When the cue comes on screen, as 0:12:03. */
  start: string;
  seconds: number;
  /** One of two speakers' lines in the cue, each written on a line of its own. */
  speakers: boolean;
  /** Characters of the cue's other passages: a cue is read as a whole. */
  other_characters: number;
};

const squeezed = (text: string) => text.replace(/\s+/g, "");

/** "0:12:03 · 2.5 s": where a cue stands in the film and how long it is on screen. */
export const cueLabel = (cue: Cue) => t("text.subtitles.cueLabel", { start: cue.start, seconds: cue.seconds.toFixed(1) });

/**
 * What a viewer could not read of a cue with this text: too much for its time
 * on screen, or more than its lines hold. The pipeline's audit makes the same
 * two checks (and breaks the lines itself, which may find a little more).
 */
export function readingProblems(text: string, cue: Cue | undefined, limits: ReadingLimits | null | undefined): string[] {
  if (!cue || !limits) return [];
  const problems: string[] = [];
  const characters = squeezed(text).length + cue.other_characters;
  const readable = Math.floor(limits.characters_per_second * cue.seconds);
  if (cue.seconds > 0 && characters > readable) {
    problems.push(t("text.subtitles.tooLong", { seconds: cue.seconds.toFixed(1), characters, readable }));
  }
  const length = text.trim().length;
  if (cue.speakers) {
    // A speaker's line is written with a dash and a space before it, and is not broken again.
    if (length + 2 > limits.line_characters) problems.push(t("text.subtitles.lineTooLong", { length: length + 2, fit: limits.line_characters }));
  } else {
    const needed = linesNeeded(text, limits.line_characters);
    if (needed > limits.lines) problems.push(t("text.subtitles.doesNotFit", { lines: limits.lines, width: limits.line_characters, needed }));
  }
  return problems;
}

/**
 * How many lines of `width` characters a cue's text takes when it is broken
 * between words: fewer characters than two lines hold may still need three,
 * when no word ends near the middle. Text without spaces breaks anywhere.
 */
export function linesNeeded(text: string, width: number): number {
  const words = text.trim().split(/\s+/).filter(Boolean);
  let lines = 0;
  let room = 0;
  for (const word of words) {
    if (word.length > width) {
      // One unbroken stretch longer than a line (a script without spaces): it starts a line and fills as many as it needs.
      lines += Math.ceil(word.length / width);
      room = width - (word.length % width || width);
      continue;
    }
    if (lines === 0 || word.length + 1 > room) {
      lines += 1;
      room = width - word.length;
    } else {
      room -= word.length + 1;
    }
  }
  return lines;
}
