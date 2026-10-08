import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { t as translate, useT } from "../i18n";

export type BookInfo = {
  name: string;
  path: string;
  size: number;
  format: string;
  title: string;
  authors: string[];
  language: string;
  has_cover: boolean;
  warning?: string;
  /** A subtitle file: how many cues it has, and the time its last one ends. */
  cues?: number;
  duration?: string;
};

/** Full-screen view of a cover; any click or Escape dismisses it (and only it). */
function CoverZoom({ src, title, onClose }: { src: string; title: string; onClose: () => void }) {
  const t = useT();
  useEffect(() => {
    // Capture phase on window runs before the dialog's own Escape handler, so
    // Escape closes this overlay without also closing the new-job dialog.
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { e.stopPropagation(); onClose(); }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return createPortal(
    <div className="cover-zoom" role="dialog" aria-modal="true" aria-label={t("jobs.book.coverOf", { title })} onClick={onClose}>
      <figure>
        <img src={src} alt={t("jobs.book.coverOf", { title })} />
        <figcaption>{t("jobs.book.coverCaption", { title })}</figcaption>
      </figure>
    </div>,
    document.body,
  );
}

function FragmentRow({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd className="mono">{value}</dd>
    </>
  );
}

const sizeText = (bytes: number) =>
  bytes >= 1 << 20 ? translate("jobs.book.sizeMb", { size: (bytes / (1 << 20)).toFixed(1) }) : translate("jobs.book.sizeKb", { size: String(Math.max(1, Math.round(bytes / 1024))) });

/** A picked source book: its cover (or a generated one), title and author, and where the file is. */
export function BookCard({
  book,
  onChange,
  extra = [],
  compact = false,
}: {
  book: BookInfo;
  onChange?: () => void;
  /** Additional label/value rows shown with File and Path, e.g. the config file. */
  extra?: [string, string][];
  compact?: boolean;
}) {
  const t = useT();
  const [coverFailed, setCoverFailed] = useState(false);
  const [zoomed, setZoomed] = useState(false);
  const title = book.title || book.name.replace(/\.[a-z]+$/i, "");
  const authors = book.authors.join(", ");
  const showCover = book.has_cover && !coverFailed;
  const coverSrc = `/api/book/cover?path=${encodeURIComponent(book.path)}`;
  return (
    <div className={`bookcard ${compact ? "compact" : ""}`}>
      <div className={`book ${showCover ? "" : "generated"}`} aria-hidden={!showCover}>
        {showCover ? (
          <button type="button" className="cover-button" title={t("jobs.book.showCover")} onClick={() => setZoomed(true)}>
            <img src={coverSrc} alt={t("jobs.book.coverOf", { title })} onError={() => setCoverFailed(true)} />
          </button>
        ) : (
          <div className="generated-cover">
            <span className="gc-title">{title}</span>
            {authors && <span className="gc-author">{authors}</span>}
          </div>
        )}
      </div>
      <div className="book-meta">
        <div className="book-title">{title}</div>
        {book.cues !== undefined ? (
          <div className="book-author">{t("jobs.book.cues", { count: book.cues, duration: book.duration })}</div>
        ) : authors ? <div className="book-author">{authors}</div> : <div className="meta">{t("jobs.book.noAuthor")}</div>}
        <div className="row" style={{ margin: "8px 0" }}>
          <span className="chip">{book.format.toUpperCase()}</span>
          {book.language && <span className="chip">{book.language}</span>}
          <span className="chip">{sizeText(book.size)}</span>
        </div>
        <dl className="book-file">
          <dt>{t("jobs.book.file")}</dt><dd className="mono">{book.name}</dd>
          <dt>{t("jobs.book.path")}</dt><dd className="mono">{book.path}</dd>
          {extra.map(([label, value]) => (
            <FragmentRow key={label} label={label} value={value} />
          ))}
        </dl>
        {book.warning && <div className="meta" style={{ color: "var(--warn)" }}>{book.warning}</div>}
        {onChange && <button type="button" className="small" onClick={onChange}>{t("jobs.book.chooseAnother")}</button>}
      </div>
      {zoomed && showCover && <CoverZoom src={coverSrc} title={title} onClose={() => setZoomed(false)} />}
    </div>
  );
}
