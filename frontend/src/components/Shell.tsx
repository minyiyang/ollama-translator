import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { LOCALES, setLocale, useLocale, useT, type Locale, type MessageKey } from "../i18n";
import { isPseudo, PSEUDO_LOCALES } from "../i18n/pseudo";
import { tierLabel } from "../lib/enums";
import { directionLabel } from "../lib/format";
import { isGeneric } from "../lib/languages";
import { tabStates } from "../lib/stages";
import { useJob } from "./JobContext";
import { JobControls } from "./JobControls";

// Pipeline order: config, then the glossary gate, then translation progress.
const TABS = [
  ["config", "shell.tab.config"],
  ["glossary", "shell.tab.glossary"],
  ["progress", "shell.tab.progress"],
  ["text", "shell.tab.text"],
  ["review", "shell.tab.review"],
] as const satisfies readonly (readonly [string, MessageKey])[];

/** The interface-language menu; each language is named in its own language. */
function LanguageMenu() {
  const t = useT();
  const locale = useLocale();
  // The layout-test languages are offered while developing, or when one is in use.
  const locales = import.meta.env.MODE === "development" || isPseudo(locale) ? [...LOCALES, ...PSEUDO_LOCALES] : LOCALES;
  return (
    <select className="language-menu" aria-label={t("shell.language")} title={t("shell.language")} value={locale}
      onChange={(event) => void setLocale(event.target.value as Locale).catch(() => undefined)}>
      {locales.map(({ code, name }) => <option key={code} value={code} lang={code}>{name}</option>)}
    </select>
  );
}

/** Sticky header with job tabs; publishes its height as --header-h for sticky children. */
export function Shell({
  jobId,
  crumb,
  tools,
  children,
}: {
  jobId?: string;
  /** Breadcrumb for pages outside a job (defaults to the section name). */
  crumb?: ReactNode;
  tools?: ReactNode;
  children: ReactNode;
}) {
  const t = useT();
  const header = useRef<HTMLElement>(null);
  const { info } = useJob();
  const dots = jobId && info ? tabStates(info.kind, info.overall, info.stages) : {};
  useLayoutEffect(() => {
    const element = header.current;
    if (!element) return;
    const sync = () =>
      document.documentElement.style.setProperty("--header-h", `${Math.ceil(element.getBoundingClientRect().height)}px`);
    sync();
    const observer = new ResizeObserver(sync);
    observer.observe(element);
    return () => observer.disconnect();
  }, []);

  return (
    <>
      <header className="shell" ref={header}>
        <Link className="brand" to="/">Ollama Translator</Link>
        {!jobId && (
          <nav className="tabs">
            <NavLink to="/" end className={({ isActive }) => (isActive ? "on" : "")}>{t("shell.jobs")}</NavLink>
            <NavLink to="/series" className={({ isActive }) => (isActive ? "on" : "")}>{t("shell.series")}</NavLink>
          </nav>
        )}
        {(jobId || crumb) && <span className={jobId ? "crumb mono" : "crumb"} title={jobId ?? undefined}>{jobId ?? crumb}</span>}
        {jobId && info?.languages && isGeneric(info.languages) && (
          <span className="chip warn" title={[
            t("shell.pairTiers", { source: info.languages.source.name, sourceTier: tierLabel(info.languages.source.tier), target: info.languages.target.name, targetTier: tierLabel(info.languages.target.tier) }),
            ...info.languages.skipped.map((item) => t("shell.skipped", { check: item.check, reason: item.reason })),
          ].join("\n")}>
            {t("shell.checksSkipped", { direction: directionLabel(info.direction), count: info.languages.skipped.length })}
          </span>
        )}
        {jobId && info?.series && (
          <Link className="chip series-chip" to={`/series/${encodeURIComponent(info.series.series_id)}`}
            title={info.series.version ? t("shell.seriesPinned", { version: info.series.version }) : t("shell.seriesNotPinned")}>
            {t("shell.seriesChip", { name: info.series.name, version: info.series.version ?? t("shell.notPinned") })}
          </Link>
        )}
        {jobId && (
          <nav className="tabs">
            {TABS.map(([key, label]) => (
              <NavLink key={key} to={`/jobs/${encodeURIComponent(jobId)}/${key}`} className={({ isActive }) => (isActive ? "on" : "")}>
                {t(label)}
                {dots[key] && (
                  <span className={`pip ${dots[key]}`} title={dots[key] === "running" ? t("shell.pip.running") : t("shell.pip.waiting")} aria-label={dots[key] === "running" ? t("shell.pip.runningLabel") : t("shell.pip.waitingLabel")} />
                )}
              </NavLink>
            ))}
          </nav>
        )}
        <span className="spacer" />
        <div className="tools">
          {tools}
          {jobId && <JobControls />}
          <LanguageMenu />
        </div>
      </header>
      {children}
    </>
  );
}
