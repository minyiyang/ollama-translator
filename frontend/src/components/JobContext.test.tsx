import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiError, deferred, mockApi } from "../test/mockApi";
import { jobInfo, renderInJob } from "../test/job";
import { NotStarted, useJob } from "./JobContext";

function Probe() {
  const { jobId, info, error, refresh } = useJob();
  return (
    <>
      <p>job: {jobId}</p>
      <p>status: {info ? info.overall : "none"}</p>
      <p>error: {error || "none"}</p>
      <button onClick={() => refresh()}>refresh</button>
      <Link to="/elsewhere">leave</Link>
      <Link to="/jobs/other">other job</Link>
    </>
  );
}

const INFO = "/api/jobs/demo/info";
const advance = (ms: number) => act(async () => { await vi.advanceTimersByTimeAsync(ms); });

afterEach(() => vi.useRealTimers());

describe("Opening a job", () => {
  it("loads the job in the address bar for the page to show", async () => {
    const api = mockApi({ "GET /api/jobs/demo/info": jobInfo({ overall: "paused" }) });
    renderInJob(<Probe />);
    expect(screen.getByText("job: demo")).toBeInTheDocument();
    expect(screen.getByText("status: none")).toBeInTheDocument(); // not loaded yet

    expect(await screen.findByText("status: paused")).toBeInTheDocument();
    expect(screen.getByText("error: none")).toBeInTheDocument();
    expect(api.count(INFO)).toBe(1);
  });

  it("opens a job whose name has a space in it", async () => {
    const api = mockApi({ "GET /api/jobs/my%20book/info": jobInfo({ job_id: "my book" }) });
    renderInJob(<Probe />, "/jobs/my%20book");
    expect(await screen.findByText("status: complete")).toBeInTheDocument();
    expect(screen.getByText("job: my book")).toBeInTheDocument();
    expect(api.requested("/api/jobs/my%20book/info")).toBe(true);
  });

  it("says why a job could not be opened", async () => {
    mockApi({ "GET /api/jobs/demo/info": apiError("job demo not found", 404) });
    renderInJob(<Probe />);
    expect(await screen.findByText("error: job demo not found")).toBeInTheDocument();
    expect(screen.getByText("status: none")).toBeInTheDocument();
  });

  it("shows the new status as soon as the page asks, after a button changed it", async () => {
    let overall = "paused";
    const api = mockApi({ "GET /api/jobs/demo/info": () => jobInfo({ overall }) });
    renderInJob(<Probe />);
    await screen.findByText("status: paused");

    overall = "running";
    await userEvent.setup().click(screen.getByRole("button", { name: "refresh" }));
    expect(await screen.findByText("status: running")).toBeInTheDocument();
    expect(api.count(INFO)).toBe(2);
  });

  it("checks on an idle job every six seconds", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi({ "GET /api/jobs/demo/info": jobInfo() });
    renderInJob(<Probe />);
    await screen.findByText("status: complete");

    await advance(5900);
    expect(api.count(INFO)).toBe(1);
    await advance(200);
    expect(api.count(INFO)).toBe(2);
  });

  it.each([
    ["a running job", { running: true, overall: "running" }],
    ["a draft that is starting", { kind: "draft", overall: "starting" }],
  ])("checks on %s every two seconds", async (_name, overrides) => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi({ "GET /api/jobs/demo/info": jobInfo(overrides) });
    renderInJob(<Probe />);
    await screen.findByText(`status: ${overrides.overall}`);

    await advance(2100);
    expect(api.count(INFO)).toBe(2);
  });

  it("keeps trying while the server is down and drops the error once it is back", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let down = true;
    mockApi({ "GET /api/jobs/demo/info": () => (down ? apiError("server restarting", 503) : jobInfo()) });
    renderInJob(<Probe />);
    await screen.findByText("error: server restarting");

    down = false;
    await advance(6100);
    expect(await screen.findByText("status: complete")).toBeInTheDocument();
    expect(screen.getByText("error: none")).toBeInTheDocument();
  });

  it("stops checking once the user leaves the job", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi({ "GET /api/jobs/demo/info": jobInfo() });
    renderInJob(<Probe />);
    await screen.findByText("status: complete");

    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("link", { name: "leave" }));
    expect(screen.getByText("at /elsewhere")).toBeInTheDocument();
    await advance(20_000);
    expect(api.count(INFO)).toBe(1);
  });
});

describe("Leaving a job before it has answered", () => {
  it("stops checking on a job the user left before it had loaded", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const gate = deferred();
    let held = false;
    const api = mockApi({ "GET /api/jobs/demo/info": () => { if (held) return jobInfo(); held = true; return gate.promise; } });
    renderInJob(<Probe />);

    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("link", { name: "leave" }));
    expect(screen.getByText("at /elsewhere")).toBeInTheDocument();
    await act(async () => { gate.resolve(jobInfo()); await vi.advanceTimersByTimeAsync(30_000); });
    expect(api.count(INFO)).toBe(1);
  });

  it("never shows one job's late answer under another job", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const gate = deferred();
    let held = false;
    const api = mockApi({
      "GET /api/jobs/demo/info": () => { if (held) return jobInfo({ overall: "paused" }); held = true; return gate.promise; },
      "GET /api/jobs/other/info": jobInfo({ job_id: "other", overall: "running", running: true }),
    });
    renderInJob(<Probe />);
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("link", { name: "other job" }));
    expect(await screen.findByText("status: running")).toBeInTheDocument();
    expect(screen.getByText("job: other")).toBeInTheDocument();

    await act(async () => { gate.resolve(jobInfo({ overall: "paused" })); await vi.advanceTimersByTimeAsync(30_000); });
    expect(screen.getByText("status: running")).toBeInTheDocument(); // still the job in the address bar
    expect(api.count(INFO)).toBe(1); // and the job that was left is not polled again
  });

  it("does not start checking twice as often after a button refreshed the job", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const api = mockApi({ "GET /api/jobs/demo/info": jobInfo() });
    renderInJob(<Probe />);
    await screen.findByText("status: complete");
    await userEvent.setup({ advanceTimers: vi.advanceTimersByTime }).click(screen.getByRole("button", { name: "refresh" }));
    await advance(100);
    expect(api.count(INFO)).toBe(2);
    await advance(6100);
    expect(api.count(INFO)).toBe(3);
  });
});

describe("A page that is not inside a job", () => {
  it("has no job, no status, and no error", () => {
    render(<MemoryRouter><Probe /></MemoryRouter>);
    expect(screen.getByText("job:")).toBeInTheDocument();
    expect(screen.getByText("status: none")).toBeInTheDocument();
    expect(screen.getByText("error: none")).toBeInTheDocument();
  });
});

describe("A tab of a job that has not started", () => {
  it("tells the user to validate and start a draft", async () => {
    mockApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "draft" }) });
    renderInJob(<NotStarted what="glossary" />);
    expect(await screen.findByText(/This job has not started yet, so there is no glossary to show/)).toHaveTextContent(
      "Validate the configuration on the Config tab, then use Start translation at the top.",
    );
  });

  it("says the run is starting while the workspace is being created", async () => {
    mockApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "starting" }) });
    renderInJob(<NotStarted what="glossary" />);
    expect(await screen.findByText("Starting the run; this page fills in once the job workspace exists.")).toBeInTheDocument();
    expect(screen.queryByText(/has not started yet/)).not.toBeInTheDocument();
  });
});
