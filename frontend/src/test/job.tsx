import { render } from "@testing-library/react";
import type { ReactNode } from "react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { DialogProvider } from "../components/Dialog";
import { JobLayout } from "../components/JobContext";
import { ToastProvider } from "../components/Toast";

/** A finished, idle job as `/api/jobs/:id/info` reports it. */
export const jobInfo = (overrides: Record<string, unknown> = {}) => ({
  job_id: "demo",
  kind: "job",
  overall: "complete",
  source: "book.epub",
  config: "demo.yaml",
  stages: [],
  running: false,
  pause_requested: false,
  can_stop: false,
  process: null,
  ...overrides,
});

export const stage = (name: string, status: string, overrides: Record<string, unknown> = {}) => ({
  name, status, attempts: 0, message: "", ...overrides,
});

/** Where the router is, for asserting a navigation. */
export function Where() {
  const { pathname } = useLocation();
  return <p>at {pathname}</p>;
}

/**
 * Render one component inside a job route, with the providers a page has: the
 * component reads the job through `useJob`, so `/api/jobs/<id>/info` must be mocked.
 * Any other route shows where the router went.
 */
export function renderInJob(ui: ReactNode, at = "/jobs/demo") {
  return render(
    <MemoryRouter initialEntries={[at]}>
      <ToastProvider>
        <DialogProvider>
          <Routes>
            <Route path="/jobs/:jobId" element={<JobLayout />}>
              <Route index element={ui} />
              <Route path="*" element={<Where />} />
            </Route>
            <Route path="*" element={<Where />} />
          </Routes>
        </DialogProvider>
      </ToastProvider>
    </MemoryRouter>,
  );
}
