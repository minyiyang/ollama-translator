import { useLayoutEffect, useRef, type ReactNode } from "react";
import { Link, NavLink } from "react-router-dom";
import { tabStates } from "../lib/stages";
import { useJob } from "./JobContext";
import { JobControls } from "./JobControls";

// Pipeline order: config, then the glossary gate, then translation progress.
const TABS = [
  ["config", "Config"],
  ["glossary", "Glossary"],
  ["progress", "Progress"],
  ["text", "Text"],
  ["review", "Final review"],
] as const;

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
            <NavLink to="/" end className={({ isActive }) => (isActive ? "on" : "")}>Jobs</NavLink>
            <NavLink to="/series" className={({ isActive }) => (isActive ? "on" : "")}>Series</NavLink>
          </nav>
        )}
        {(jobId || crumb) && <span className={jobId ? "crumb mono" : "crumb"}>{jobId ?? crumb}</span>}
        {jobId && info?.series && (
          <Link className="chip series-chip" to={`/series/${encodeURIComponent(info.series.series_id)}`}
            title={info.series.version ? `Pinned to series glossary ${info.series.version}` : "In this series; not pinned to a version yet"}>
            Series {info.series.name} · {info.series.version ?? "not pinned"}
          </Link>
        )}
        {jobId && (
          <nav className="tabs">
            {TABS.map(([key, label]) => (
              <NavLink key={key} to={`/jobs/${encodeURIComponent(jobId)}/${key}`} className={({ isActive }) => (isActive ? "on" : "")}>
                {label}
                {dots[key] && (
                  <span className={`pip ${dots[key]}`} title={dots[key] === "running" ? "Running now" : "Waiting for you"} aria-label={dots[key] === "running" ? "running" : "waiting for you"} />
                )}
              </NavLink>
            ))}
          </nav>
        )}
        <span className="spacer" />
        <div className="tools">
          {tools}
          {jobId && <JobControls />}
        </div>
      </header>
      {children}
    </>
  );
}
