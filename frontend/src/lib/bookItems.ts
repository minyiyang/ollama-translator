/**
 * What the book says of itself: its title, the contents entries that are no passage, its
 * notes, and its pictures' descriptions. The title stage translates them; the Text tab shows
 * them beside the passages, editable the same way (book_agent/book_edits.py).
 */

import type { MessageKey } from "../i18n";

export type BookItemKind = "title" | "contents" | "note" | "description";

export const BOOK_DOCUMENT = "BOOK";

/** Each kind's name, as a message to translate where it is shown. */
export const KIND_LABELS: Record<BookItemKind, MessageKey> = {
  title: "text.kind.title",
  contents: "text.kind.contents",
  note: "text.kind.note",
  description: "text.kind.description",
};

/** An item the title stage left in the source language, as the review page lists it. */
export type LeftInSource = {
  item_id: string;
  kind: BookItemKind;
  document_id: string;
  chapter: string;
  source: string;
};

/** The Text tab, opened at one chapter, and at one row of it. */
export function textLink(jobId: string, documentId: string, itemId?: string): string {
  const query = new URLSearchParams({ chapter: documentId, ...(itemId ? { item: itemId } : {}) });
  return `/jobs/${encodeURIComponent(jobId)}/text?${query.toString()}`;
}

/**
 * The chapter a note or a passage stands in, from its id ("D0001-N000002" is in the document
 * at order 1), among the chapters the outline lists.
 */
export function chapterOf(rowId: string, chapters: ReadonlyArray<{ document_id: string; order: number }>): string {
  const match = /^D(\d{4})-[SN]\d{6}$/.exec(rowId);
  if (!match) return "";
  return chapters.find((chapter) => chapter.order === Number(match[1]))?.document_id ?? "";
}

/** Where a picture of the book is served from. */
export function pictureUrl(jobId: string, path: string): string {
  return `/api/jobs/${encodeURIComponent(jobId)}/text/picture?path=${encodeURIComponent(path)}`;
}

/** The number a note's reference shows, from the note's first marker ("<I000>2</I000> ..."), if it begins with one. */
export function noteNumber(source: string): string {
  const match = /^<I\d{3}>([^<]{1,6})<\/I\d{3}>/.exec(source.trim());
  return match ? match[1].trim() : "";
}
