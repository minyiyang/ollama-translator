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
  ["review", "Final review"],
] as const;

/** Sticky header with job tabs; publishes its height as --header-h for sticky children. */
export function Shell({
  jobId,
  tools,
  children,
}: {
  jobId?: string;
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
        <span className={jobId ? "crumb mono" : "crumb"}>{jobId ?? "Jobs"}</span>
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
