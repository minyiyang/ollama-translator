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
export const cueLabel = (cue: Cue) => `${cue.start} · ${cue.seconds.toFixed(1)} s`;

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
    problems.push(`too long to read in ${cue.seconds.toFixed(1)} s: ${characters} characters, about ${readable} can be read`);
  }
  const length = text.trim().length;
  if (cue.speakers) {
    // A speaker's line is written with a dash and a space before it, and is not broken again.
    if (length + 2 > limits.line_characters) problems.push(`line too long: ${length + 2} characters, ${limits.line_characters} fit`);
  } else if (length > limits.lines * limits.line_characters) {
    problems.push(`does not fit ${limits.lines} lines of ${limits.line_characters}: ${length} characters`);
  }
  return problems;
}
