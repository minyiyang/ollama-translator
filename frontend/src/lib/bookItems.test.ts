import { describe, expect, it } from "vitest";
import { chapterOf, noteNumber, pictureUrl, textLink } from "./bookItems";

const chapters = [
  { document_id: "BOOK", order: -1 },
  { document_id: "chapter", order: 0 },
  { document_id: "endnotes", order: 1 },
];

describe("the book's own items", () => {
  it("finds the chapter a note or a passage stands in from its id", () => {
    expect(chapterOf("D0001-N000002", chapters)).toBe("endnotes");
    expect(chapterOf("D0000-S000003", chapters)).toBe("chapter");
    // The title has no chapter of its own to be found in; nor has an id from a book that is gone.
    expect(chapterOf("BOOK-T", chapters)).toBe("");
    expect(chapterOf("D0007-N000001", chapters)).toBe("");
  });

  it("reads a note's number from the link it begins with, as the reader sees it", () => {
    expect(noteNumber("<I000>2</I000> Mrs. Hudson kept the house.")).toBe("2");
    expect(noteNumber("<I000>iv</I000> A note numbered in Roman.")).toBe("iv");
    expect(noteNumber("A seven-per-cent solution. <I000>Back</I000>")).toBe(""); // its link is at its end
    expect(noteNumber("Endnotes")).toBe("");
  });

  it("links to the Text tab at a chapter and a row, and to a picture of the book", () => {
    expect(textLink("alice de", "chapter", "D0000-N000001")).toBe("/jobs/alice%20de/text?chapter=chapter&item=D0000-N000001");
    expect(textLink("demo", "BOOK")).toBe("/jobs/demo/text?chapter=BOOK");
    expect(pictureUrl("demo", "OEBPS/images/plan of the room.png")).toBe(
      "/api/jobs/demo/text/picture?path=OEBPS%2Fimages%2Fplan%20of%20the%20room.png",
    );
  });
});
