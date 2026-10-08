import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { mockApi } from "../test/mockApi";
import { jobInfo, renderInJob, stage } from "../test/job";
import { Shell } from "./Shell";

const renderOutside = (ui: React.ReactNode, at = "/") => render(<MemoryRouter initialEntries={[at]}>{ui}</MemoryRouter>);

async function renderJobShell(info: unknown, at = "/jobs/demo") {
  mockApi({ [`GET /api${at}/info`]: info });
  const jobId = decodeURIComponent(at.split("/")[2]);
  renderInJob(<Shell jobId={jobId}><p>page</p></Shell>, at);
  await screen.findByText((info as { overall: string }).overall); // the status chip: the job has loaded
}

const tabs = () => within(screen.getByRole("navigation")).getAllByRole("link");
const tab = (name: RegExp | string) => within(screen.getByRole("navigation")).getByRole("link", { name });

afterEach(() => document.documentElement.style.removeProperty("--header-h"));

describe("The header", () => {
  describe("outside a job", () => {
    it("links the brand home and offers the Jobs and Series sections", () => {
      renderOutside(<Shell><p>page</p></Shell>);
      expect(screen.getByRole("link", { name: "Ollama Translator" })).toHaveAttribute("href", "/");
      expect(tabs().map((link) => [link.textContent, link.getAttribute("href")])).toEqual([["Jobs", "/"], ["Series", "/series"]]);
      expect(screen.getByText("page")).toBeInTheDocument();
    });

    it("marks the section the user is in", () => {
      const { unmount } = renderOutside(<Shell>page</Shell>);
      expect(tab("Jobs")).toHaveAttribute("aria-current", "page");
      expect(tab("Series")).not.toHaveAttribute("aria-current");
      unmount();

      renderOutside(<Shell>page</Shell>, "/series/qel");
      expect(tab("Series")).toHaveAttribute("aria-current", "page");
      expect(tab("Jobs")).not.toHaveAttribute("aria-current"); // "/" matches exactly, not as a prefix
    });

    it("shows a breadcrumb and the page's tools", () => {
      renderOutside(<Shell crumb="The Qel Cycle" tools={<button>Export</button>}>page</Shell>, "/series/qel");
      expect(screen.getByText("The Qel Cycle")).toBeInTheDocument();
      expect(within(screen.getByRole("banner")).getByRole("button", { name: "Export" })).toBeInTheDocument();
    });

    it("has no job tabs or job controls", () => {
      renderOutside(<Shell>page</Shell>);
      expect(screen.queryByRole("link", { name: "Config" })).not.toBeInTheDocument();
      expect(within(screen.getByRole("banner")).queryByRole("button")).not.toBeInTheDocument();
    });
  });

  describe("inside a job", () => {
    it("shows the job id and the five tabs in pipeline order", async () => {
      await renderJobShell(jobInfo());
      expect(within(screen.getByRole("banner")).getByText("demo")).toBeInTheDocument();
      expect(tabs().map((link) => [link.textContent, link.getAttribute("href")])).toEqual([
        ["Config", "/jobs/demo/config"],
        ["Glossary", "/jobs/demo/glossary"],
        ["Progress", "/jobs/demo/progress"],
        ["Text", "/jobs/demo/text"],
        ["Final review", "/jobs/demo/review"],
      ]);
      expect(screen.queryByRole("link", { name: "Series" })).not.toBeInTheDocument();
    });

    it("links the tabs of a job whose name has a space in it", async () => {
      await renderJobShell(jobInfo({ job_id: "my book" }), "/jobs/my%20book");
      expect(tab("Progress")).toHaveAttribute("href", "/jobs/my%20book/progress");
      expect(within(screen.getByRole("banner")).getByText("my book")).toBeInTheDocument();
    });

    it("puts the job's status and controls next to the page's tools", async () => {
      await renderJobShell(jobInfo({ overall: "paused", stages: [stage("translate", "paused")] }));
      const header = screen.getByRole("banner");
      expect(within(header).getByText("paused")).toBeInTheDocument();
      expect(within(header).getByRole("button", { name: "▶ Resume" })).toBeInTheDocument();
    });

    it("dots the tab whose stage is running", async () => {
      await renderJobShell(jobInfo({ overall: "running", running: true, stages: [stage("decompile", "completed"), stage("translate", "running")] }));
      expect(within(tab(/Progress/)).getByLabelText("running")).toHaveAttribute("title", "Running now");
      expect(screen.getAllByLabelText(/running|waiting for you/)).toHaveLength(1);
    });

    it("dots the tab that is waiting for the user", async () => {
      await renderJobShell(jobInfo({ overall: "paused", stages: [stage("decompile", "completed"), stage("approve_glossary", "paused")] }));
      expect(within(tab(/Glossary/)).getByLabelText("waiting for you")).toHaveAttribute("title", "Waiting for you");
    });

    it("dots the Config tab of a draft", async () => {
      await renderJobShell(jobInfo({ kind: "draft", overall: "draft" }));
      expect(within(tab(/Config/)).getByLabelText("waiting for you")).toBeInTheDocument();
    });

    it("dots no tab of a finished job", async () => {
      await renderJobShell(jobInfo({ stages: [stage("decompile", "completed")] }));
      expect(screen.queryByLabelText(/running|waiting for you/)).not.toBeInTheDocument();
    });

    it("warns that the book's languages are less supported and lists the checks skipped", async () => {
      await renderJobShell(jobInfo({
        direction: "en>ja",
        languages: {
          pair: "en>ja",
          source: { code: "en", name: "English", tier: "tuned" },
          target: { code: "ja", name: "Japanese", tier: "generic" },
          skipped: [{ check: "prose rewrite", reason: "written for Chinese" }, { check: "punctuation conventions", reason: "no house conventions" }],
          notice: "",
        },
      }));
      const chip = screen.getByText("EN → JA · 2 checks skipped");
      expect(chip).toHaveAttribute(
        "title",
        "English (tuned) → Japanese (generic).\nSkipped: prose rewrite (written for Chinese).\nSkipped: punctuation conventions (no house conventions).",
      );
    });

    it("has no such warning for English into Chinese", async () => {
      await renderJobShell(jobInfo({
        direction: "en-zh",
        languages: {
          pair: "en-zh",
          source: { code: "en", name: "English", tier: "tuned" },
          target: { code: "zh", name: "Simplified Chinese", tier: "tuned" },
          skipped: [],
          notice: "",
        },
      }));
      expect(screen.queryByText(/checks skipped/)).not.toBeInTheDocument();
    });

    it("links to the job's series with the pinned glossary version", async () => {
      await renderJobShell(jobInfo({ series: { series_id: "qel cycle", name: "The Qel Cycle", version: "v002", latest: "v003" } }));
      const chip = screen.getByRole("link", { name: "Series The Qel Cycle · v002" });
      expect(chip).toHaveAttribute("href", "/series/qel%20cycle");
      expect(chip).toHaveAttribute("title", "Pinned to series glossary v002");
    });

    it("says when the job is in a series but not pinned yet", async () => {
      await renderJobShell(jobInfo({ series: { series_id: "qel", name: "The Qel Cycle", version: null, latest: "v001" } }));
      expect(screen.getByRole("link", { name: "Series The Qel Cycle · not pinned" })).toHaveAttribute("title", "In this series; not pinned to a version yet");
    });
  });

  it("tells the page how tall it is, so sticky toolbars sit just below it", () => {
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue({ height: 57.2 } as DOMRect);
    renderOutside(<Shell>page</Shell>);
    expect(document.documentElement.style.getPropertyValue("--header-h")).toBe("58px");
  });
});

describe("The interface-language menu", () => {
  const menu = () => screen.getByRole("combobox", { name: "Interface language" });

  it("offers each language under its own name, starting in English", () => {
    renderOutside(<Shell>page</Shell>);
    expect(menu()).toHaveValue("en");
    expect(within(menu()).getAllByRole("option").map((option) => option.textContent))
      .toEqual(["English", "简体中文", "日本語", "Français", "Español", "Deutsch", "한국어"]);
  });

  it("translates the header at once and remembers the choice", async () => {
    renderOutside(<Shell>page</Shell>);
    await userEvent.selectOptions(menu(), "简体中文");
    expect(await screen.findByRole("combobox", { name: /界面语言|语言/ })).toHaveValue("zh-CN");
    expect(tabs().map((link) => link.textContent)).not.toContain("Jobs");
    expect(tabs().map((link) => link.getAttribute("href"))).toEqual(["/", "/series"]);
    expect(document.documentElement.lang).toBe("zh-CN");
    expect(window.localStorage.getItem("ui-language")).toBe("zh-CN");
  });
});
