import { fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { BookCard, type BookInfo } from "./BookCard";

const book = (overrides: Partial<BookInfo> = {}): BookInfo => ({
  name: "alice.epub",
  path: "C:\\runs\\.uploads\\alice & co.epub",
  size: 3 * 1024 * 1024 + 512 * 1024,
  format: "epub",
  title: "Alice's Adventures in Wonderland",
  authors: ["Lewis Carroll", "John Tenniel"],
  language: "en",
  has_cover: true,
  ...overrides,
});

const TITLE = "Alice's Adventures in Wonderland";
const row = (term: string) => screen.getByText(term).nextElementSibling;
// The cover is a button named by its image; its tooltip says what a click does.
const coverButton = () => screen.getByTitle("Show the cover larger");

describe("A book's card", () => {
  it("shows the book's title, authors, format, language, and size", () => {
    render(<BookCard book={book()} />);
    expect(screen.getByText(TITLE)).toBeInTheDocument();
    expect(screen.getByText("Lewis Carroll, John Tenniel")).toBeInTheDocument();
    expect(screen.getByText("EPUB")).toBeInTheDocument();
    expect(screen.getByText("en")).toBeInTheDocument();
    expect(screen.getByText("3.5 MB")).toBeInTheDocument();
  });

  it("shows where the file is, and the config it goes with", () => {
    render(<BookCard book={book()} extra={[["Config", "alice.yaml"]]} />);
    expect(row("File")).toHaveTextContent("alice.epub");
    expect(row("Path")).toHaveTextContent("C:\\runs\\.uploads\\alice & co.epub");
    expect(row("Config")).toHaveTextContent("alice.yaml");
  });

  it("falls back to the file name without its extension when the book has no title", () => {
    render(<BookCard book={book({ title: "", name: "robinson-crusoe.RTF", format: "rtf", has_cover: false })} />);
    expect(screen.getAllByText("robinson-crusoe")).toHaveLength(2); // generated cover + heading
    expect(screen.getByText("RTF")).toBeInTheDocument();
  });

  it("says when no author is recorded and leaves out a missing language", () => {
    render(<BookCard book={book({ authors: [], language: "" })} />);
    expect(screen.getByText("Author not recorded")).toBeInTheDocument();
    expect(screen.queryByText("en")).not.toBeInTheDocument();
  });

  it("gives small files in KB, never less than 1", () => {
    const { rerender } = render(<BookCard book={book({ size: 300 * 1024 })} />);
    expect(screen.getByText("300 KB")).toBeInTheDocument();
    rerender(<BookCard book={book({ size: 12 })} />);
    expect(screen.getByText("1 KB")).toBeInTheDocument();
  });

  it("passes on a warning about the file", () => {
    render(<BookCard book={book({ warning: "This EPUB is DRM-protected." })} />);
    expect(screen.getByText("This EPUB is DRM-protected.")).toBeInTheDocument();
  });

  it("offers to choose another book only when it can be changed", async () => {
    const onChange = vi.fn();
    const { rerender } = render(<BookCard book={book()} />);
    expect(screen.queryByRole("button", { name: "Choose another file…" })).not.toBeInTheDocument();

    rerender(<BookCard book={book()} onChange={onChange} />);
    await userEvent.setup().click(screen.getByRole("button", { name: "Choose another file…" }));
    expect(onChange).toHaveBeenCalledOnce();
  });

  describe("cover", () => {
    it("shows the book's own cover", () => {
      render(<BookCard book={book()} />);
      expect(screen.getByRole("img", { name: `Cover of ${TITLE}` })).toHaveAttribute(
        "src",
        `/api/book/cover?path=${encodeURIComponent("C:\\runs\\.uploads\\alice & co.epub")}`,
      );
    });

    it("draws a cover from the title and authors when the book has none", () => {
      const { container } = render(<BookCard book={book({ has_cover: false })} />);
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
      expect(screen.queryByTitle("Show the cover larger")).not.toBeInTheDocument();
      const generated = container.querySelector(".generated-cover") as HTMLElement;
      expect(generated).toHaveTextContent(TITLE);
      expect(generated).toHaveTextContent("Lewis Carroll, John Tenniel");
    });

    it("falls back to the drawn cover when the image fails to load", () => {
      const { container } = render(<BookCard book={book()} />);
      fireEvent.error(screen.getByRole("img"));
      expect(screen.queryByRole("img")).not.toBeInTheDocument();
      expect(container.querySelector(".generated-cover")).toHaveTextContent(TITLE);
    });

    it("opens larger on a click and closes on a click anywhere", async () => {
      const user = userEvent.setup();
      render(<BookCard book={book()} />);
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

      expect(coverButton()).toBe(screen.getByRole("button", { name: `Cover of ${TITLE}` }));
      await user.click(coverButton());
      const zoom = screen.getByRole("dialog", { name: `Cover of ${TITLE}` });
      expect(within(zoom).getByRole("img", { name: `Cover of ${TITLE}` })).toHaveAttribute("src", expect.stringContaining("/api/book/cover?path="));
      expect(zoom).toHaveTextContent(`${TITLE} · click anywhere to close`);

      await user.click(within(zoom).getByRole("img"));
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("closes on Escape, leaving the new-job dialog underneath open", async () => {
      const underneath = vi.fn();
      window.addEventListener("keydown", underneath);
      try {
        const user = userEvent.setup();
        render(<BookCard book={book()} />);
        await user.click(coverButton());

        await user.keyboard("{Escape}");
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(underneath).not.toHaveBeenCalled();

        await user.keyboard("{Escape}"); // nothing left to close: the key goes through
        expect(underneath).toHaveBeenCalledOnce();
      } finally {
        window.removeEventListener("keydown", underneath);
      }
    });

    it("stays open when another key is pressed", async () => {
      const user = userEvent.setup();
      render(<BookCard book={book()} />);
      await user.click(coverButton());
      await user.keyboard("a");
      expect(screen.getByRole("dialog")).toBeInTheDocument();
    });
  });
});
