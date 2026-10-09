import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Outlet, useParams } from "react-router-dom";
import { jobApi } from "../api";
import { rich, useT, type MessageKey } from "../i18n";
import type { LanguageSupport } from "../lib/languages";
import type { Stage } from "../lib/stages";

export type JobInfo = {
  job_id: string;
  kind: "draft" | "job";
  /** A book, or a subtitle file; a server from before subtitle jobs does not say. */
  job_type?: "book" | "subtitles";
  /** For a subtitle job: what each other format does not carry over, as codes (subtitles.note.*). */
  output_notes?: Record<string, string[]>;
  overall: string;
  source: string;
  source_path?: string;
  config: string;
  direction?: string;
  /** The pair's tiers and skipped checks (docs/GENERIC_LANGUAGES.md, 2.3). */
  languages?: LanguageSupport | null;
  downloadable?: boolean;
  /** The format the job gives its book back in, and the formats it can be had in (docs/OUTPUT_AND_CONFIG_UX.md). */
  output_format?: string;
  output_formats?: string[];
  series?: { series_id: string; name: string; version: string | null; latest: string | null } | null;
  validated?: boolean;
  stages: Stage[];
  running: boolean;
  pause_requested: boolean;
  can_stop: boolean;
  process: {
    label: string;
    running: boolean;
    exit_code: number | null;
    outcome: "running" | "completed" | "paused" | "cancelled" | "failed";
    started: string;
    output_tail: string;
  } | null;
};

type JobState = { jobId: string; info: JobInfo | null; error: string; refresh: () => Promise<void> };

const JobContext = createContext<JobState>({ jobId: "", info: null, error: "", refresh: async () => {} });

/** Route element for /jobs/:jobId/*: polls the job's status once for the header and every tab. */
export function JobLayout() {
  const { jobId = "" } = useParams();
  const [info, setInfo] = useState<JobInfo | null>(null);
  const [error, setError] = useState("");
  const timer = useRef<number | undefined>(undefined);
  // Bumped when the layout leaves or changes job, so a request still in flight is dropped.
  const visit = useRef(0);

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    const mine = visit.current;
    let fast = false;
    try {
      const next = await jobApi<JobInfo>(jobId, "info");
      if (mine !== visit.current) return;
      setInfo(next);
      setError("");
      fast = next.running || next.overall === "starting";
    } catch (e) {
      if (mine !== visit.current) return;
      setError((e as Error).message);
    }
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(refresh, fast ? 2000 : 6000);
  }, [jobId]);

  useEffect(() => {
    setInfo(null);
    refresh();
    return () => { visit.current += 1; window.clearTimeout(timer.current); };
  }, [refresh]);

  return (
    <JobContext.Provider value={{ jobId, info, error, refresh }}>
      <Outlet />
    </JobContext.Provider>
  );
}

export const useJob = () => useContext(JobContext);

// One whole sentence per tab, so a translation can inflect around the thing that is missing.
const NOT_STARTED = {
  glossary: "jobs.notStarted.glossary",
  progress: "jobs.notStarted.progress",
  review: "jobs.notStarted.reviewQueue",
  text: "jobs.notStarted.bookText",
} as const satisfies Record<string, MessageKey>;

/** Shown on tabs that only have content once a draft job has started. */
export function NotStarted({ what }: { what: keyof typeof NOT_STARTED }) {
  const t = useT();
  const { info } = useJob();
  if (info?.overall === "starting") {
    return <div className="banner info">{t("jobs.notStarted.starting")}</div>;
  }
  return (
    <div className="banner info">
      {rich(NOT_STARTED[what], { b: (chunks) => <b>{chunks}</b> })}
    </div>
  );
}
