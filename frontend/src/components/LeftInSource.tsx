import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { jobApi } from "../api";
import { useT } from "../i18n";
import { KIND_LABELS, textLink, type LeftInSource } from "../lib/bookItems";
import { Chip } from "./ui";

/**
 * What the title stage left in the source language (a note the model gave back without its
 * link, a contents entry no model answered), until someone translates it on the Text tab.
 * None of it stops the book from compiling. `when` holds off asking until the stage has run.
 */
export function useLeftInSource(jobId: string, when: boolean): LeftInSource[] {
  const [items, setItems] = useState<LeftInSource[]>([]);
  useEffect(() => {
    if (!when) {
      setItems([]);
      return;
    }
    let live = true;
    jobApi<{ items: LeftInSource[] }>(jobId, "text/untranslated")
      .then((payload) => { if (live) setItems(payload.items); })
      .catch(() => { if (live) setItems([]); });
    return () => { live = false; };
  }, [jobId, when]);
  return items;
}

export function LeftInSourceCard({ jobId, items }: { jobId: string; items: LeftInSource[] }) {
  const t = useT();
  if (items.length === 0) return null;
  return (
    <section className="card" aria-label={t("text.leftInSource.label")}>
      <h2>{t("text.leftInSource.heading", { count: items.length })}</h2>
      <p className="meta">{t("text.leftInSource.explain")}</p>
      <table className="grid">
        <thead>
          <tr><th>{t("text.leftInSource.what")}</th><th>{t("text.leftInSource.where")}</th><th>{t("text.table.source")}</th><th /></tr>
        </thead>
        <tbody>
          {items.map((item) => (
            <tr key={item.item_id}>
              <td><Chip>{t(KIND_LABELS[item.kind])}</Chip></td>
              <td>{item.chapter}</td>
              <td>{item.source}</td>
              <td><Link to={textLink(jobId, item.document_id, item.item_id)}>{t("text.leftInSource.translate")}</Link></td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  );
}
