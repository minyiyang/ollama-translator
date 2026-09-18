import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import { Outlet, useParams } from "react-router-dom";
import { jobApi } from "../api";
import type { Stage } from "../lib/stages";

export type JobInfo = {
  job_id: string;
  kind: "draft" | "job";
  overall: string;
  source: string;
  source_path?: string;
  config: string;
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

  const refresh = useCallback(async () => {
    window.clearTimeout(timer.current);
    let fast = false;
    try {
      const next = await jobApi<JobInfo>(jobId, "info");
      setInfo(next);
      setError("");
      fast = next.running || next.overall === "starting";
    } catch (e) {
      setError((e as Error).message);
    }
    timer.current = window.setTimeout(refresh, fast ? 2000 : 6000);
  }, [jobId]);

  useEffect(() => {
    setInfo(null);
    refresh();
    return () => window.clearTimeout(timer.current);
  }, [refresh]);

  return (
    <JobContext.Provider value={{ jobId, info, error, refresh }}>
      <Outlet />
    </JobContext.Provider>
  );
}

export const useJob = () => useContext(JobContext);

/** Shown on tabs that only have content once a draft job has started. */
export function NotStarted({ what }: { what: string }) {
  const { info } = useJob();
  if (info?.overall === "starting") {
    return <div className="banner info">Starting the run; this page fills in once the job workspace exists.</div>;
  }
  return (
    <div className="banner info">
      This job has not started yet, so there is no {what} to show. Validate the configuration on the <b>Config</b> tab, then use <b>Start translation</b> at the top.
    </div>
  );
}
