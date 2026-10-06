import { act, cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "../App";
import { apiError, mockApi } from "../test/mockApi";
import { jobInfo, stage } from "../test/job";

// -- fixtures -----------------------------------------------------------------

const ACCEPT_REASON = "Verified against source; the audit finding is a false positive.";
const REPLACE_REASON = "Corrected the mistranslation identified by the audit.";

const resolution = (segment_id: string, source_text: string, current_translation: string, overrides: Record<string, unknown> = {}) => ({
  segment_id,
  source_text,
  current_translation,
  findings: [`${segment_id} was flagged`],
  suggested_fixes: [],
  decision: "pending",
  translated_text: null as string | null,
  replacements: [],
  reason: "",
  ...overrides,
});

const CATS = resolution("D0000-S000001", "She has three cats.", "她有四只猫。");
const HELLO = resolution("D0000-S000002", "Hello.", "你好。", { decision: "accept", reason: ACCEPT_REASON });
const BYE = resolution("D0001-S000001", "Goodbye.", "再见。");
const accepted = (res: ReturnType<typeof resolution>) => ({ ...res, decision: "accept", reason: ACCEPT_REASON });

const CONTEXT = {
  "D0000-S000001": {
    chapter_title: "Chapter One",
    review_kind: "audit",
    previous_source_context: "Alice lived alone.",
    next_source_context: "They slept all day.",
    findings: [
      { message: "The number changed.", severity: "high", category: "numbers", origin: "audit", source_quote: "three", translation_quote: "四" },
      { message: "The number changed." }, // reported twice: listed once
      { message: "Reads stiffly." },
    ],
    translation_versions: [
      { stage: "translate", text: "她有四只猫。" },
      { stage: "repair", text: "她有三只猫咪。" },
      { stage: "reprose", text: "" },
    ],
  },
  "D0000-S000002": { chapter_title: "Chapter One" },
};

const worksheet = (resolutions: ReturnType<typeof resolution>[] = [CATS, HELLO, BYE]) => ({
  schema_version: 1, workspace: "runs/demo", draft_output_hash: "abc123", instructions: ["Decide each segment."], resolutions,
});

const PAUSED = [stage("translate", "completed"), stage("compile", "paused")];

const language = (code: string, name: string, tier = "tuned") => ({ code, name, tier });
/** The job as the server describes it, for a book translated between these two languages. */
const jobIn = (direction: string, source: ReturnType<typeof language>, target: ReturnType<typeof language>) =>
  jobInfo({ overall: "paused", stages: PAUSED, direction, languages: { pair: direction, source, target, skipped: [], notice: "" } });
const EN_ZH = jobIn("en-zh", language("en", "English"), language("zh", "Simplified Chinese"));
const IDLE = { state: "idle", events: [], result: null };

const review = (overrides: Record<string, unknown> = {}) => ({
  worksheet: worksheet(),
  context: CONTEXT,
  stale: false,
  status: { job_id: "demo", overall: "paused", stages: PAUSED, configuration: { source_path: "" } },
  compile: IDLE,
  compile_limit: 0,
  ...overrides,
});

const BASE = "/api/jobs/demo/review";

function reviewApi(overrides: Record<string, unknown> = {}) {
  return mockApi({
    "GET /api/jobs/demo/info": EN_ZH,
    [`GET ${BASE}`]: review(),
    [`POST ${BASE}/check`]: { blocking: [] },
    [`POST ${BASE}/save`]: { saved: true },
    [`POST ${BASE}/apply`]: { report: { passed: true, review_segment_ids: [] }, approved: true },
    [`POST ${BASE}/compile`]: { started: true },
    [`GET ${BASE}/compile`]: IDLE,
    ...overrides,
  });
}

async function renderReviewTab() {
  window.history.pushState({}, "", "/jobs/demo/review");
  const user = userEvent.setup();
  render(<App />);
  return user;
}

/** Render and wait for the first segment of the queue. */
async function renderQueue() {
  const user = await renderReviewTab();
  await screen.findByRole("heading", { name: "Your translation" });
  return user;
}

const sidebar = () => within(screen.getByRole("complementary", { name: "Segments" }));
const title = () => (document.querySelector(".seghead .title") as HTMLElement).textContent;
const sectionOf = (heading: RegExp | string) => screen.getByRole("heading", { name: heading }).closest("section") as HTMLElement;
const editor = () => within(sectionOf("Your translation")).getAllByRole("textbox")[0] as HTMLTextAreaElement;
const decision = (name: string) => within(sectionOf("Your translation")).getByRole("button", { name });
const header = () => within(screen.getByRole("banner"));
const savedSheets = (api: ReturnType<typeof reviewApi>) => api.posted(`${BASE}/save`) as { worksheet: ReturnType<typeof worksheet> }[];
const BLOCKING = { blocking: [{ category: "structure", severity: "high", message: "protected marker removed" }] };

beforeEach(() => {
  // jsdom implements neither; the page scrolls to the top and to the active sidebar item when moving.
  Element.prototype.scrollIntoView = vi.fn();
  vi.spyOn(window, "scrollTo").mockImplementation(() => {});
});

// -- tests --------------------------------------------------------------------

describe("Final review tab", () => {
  describe("when there is nothing to review", () => {
    it("tells a draft job to start first, without looking for a review queue", async () => {
      const api = reviewApi({ "GET /api/jobs/demo/info": jobInfo({ kind: "draft", overall: "draft" }) });
      await renderReviewTab();
      expect(await screen.findByText(/there is no review queue to show/)).toBeInTheDocument();
      expect(api.requested(BASE)).toBe(false);
    });

    it("says why the review queue could not be opened", async () => {
      reviewApi({ [`GET ${BASE}`]: apiError("worksheet is not valid JSON", 500) });
      await renderReviewTab();
      expect(await screen.findByText("worksheet is not valid JSON")).toBeInTheDocument();
      expect(screen.queryByText("Loading…")).not.toBeInTheDocument();
    });

    it.each([
      ["no worksheet exists", { worksheet: null }, "No final-review worksheet exists for this job yet."],
      ["the worksheet is stale", { stale: true }, "The review queue has been resolved or the draft changed since this worksheet was written. Compile to regenerate reports."],
      ["the queue is empty", { worksheet: worksheet([]) }, "The review queue is empty."],
    ])("says so when %s, and offers to compile", async (_case, overrides, message) => {
      reviewApi({ [`GET ${BASE}`]: review(overrides) });
      await renderReviewTab();
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Compile now" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Apply decisions" })).not.toBeInTheDocument();
      expect(screen.queryByRole("heading", { name: "Your translation" })).not.toBeInTheDocument();
    });

    it("says a complete job has nothing left, with no compile card", async () => {
      reviewApi({
        "GET /api/jobs/demo/info": jobInfo(),
        [`GET ${BASE}`]: review({ worksheet: null, status: { job_id: "demo", overall: "complete", stages: [], configuration: { source_path: "" } } }),
      });
      await renderReviewTab();
      expect(await screen.findByText("This job is complete; there is nothing left to review.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Compile now" })).not.toBeInTheDocument();
    });
  });

  describe("the queue", () => {
    it("opens the first segment with its source, context, current translation, and editor", async () => {
      reviewApi();
      await renderQueue();
      expect(title()).toBe("D0000-S000001");
      const head = document.querySelector(".seghead") as HTMLElement;
      expect(within(head).getByText("Chapter One")).toBeInTheDocument();
      expect(within(head).getByText("audit")).toBeInTheDocument();
      expect(head).toHaveTextContent("2 of 3"); // resolved segments are listed first

      const source = sectionOf("Source");
      expect(source).toHaveTextContent("Alice lived alone.She has three cats.They slept all day.");
      expect(sectionOf("Current translation")).toHaveTextContent("她有四只猫。");
      expect(editor()).toHaveValue("她有四只猫。");
      expect(sectionOf("Your translation")).toHaveTextContent("6 chars (current 6)");
      expect(screen.queryByText("Changes vs current")).not.toBeInTheDocument();
    });

    it("groups the segments into resolved and to review, with their chapters", async () => {
      reviewApi();
      await renderQueue();
      const side = sidebar();
      expect(side.getByText("Resolved (1)")).toBeInTheDocument();
      expect(side.getByText("To review (2)")).toBeInTheDocument();
      expect(side.getAllByRole("button").slice(1).map((b) => b.textContent)).toEqual([
        "D0000-S000002Chapter One", "D0000-S000001Chapter One", "D0001-S000001",
      ]);
      expect(header().getByText(/decided/)).toHaveTextContent("1/3 decided");
    });

    it("opens the segment picked in the sidebar and scrolls back to the top", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.click(sidebar().getByRole("button", { name: /D0001-S000001/ }));
      expect(title()).toBe("D0001-S000001");
      expect(editor()).toHaveValue("再见。");
      expect(sectionOf("Source")).toHaveTextContent(/^SourceGoodbye\.$/); // no context recorded for this one
      expect(window.scrollTo).toHaveBeenCalledWith(0, 0);
      expect(Element.prototype.scrollIntoView).toHaveBeenCalled();
    });

    it("moves through the queue in sidebar order with Alt+↓ and Alt+↑, stopping at the ends", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
      expect(title()).toBe("D0001-S000001");
      await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
      expect(title()).toBe("D0001-S000001");

      await user.keyboard("{Alt>}{ArrowUp}{ArrowUp}{/Alt}");
      expect(title()).toBe("D0000-S000002");
      await user.keyboard("{Alt>}{ArrowUp}{/Alt}");
      expect(title()).toBe("D0000-S000002");
    });

    it("shows a rewrite and a reason saved in an earlier session", async () => {
      reviewApi({
        [`GET ${BASE}`]: review({ worksheet: worksheet([{ ...CATS, decision: "replace", translated_text: "她有三只猫。", reason: "Checked with the author." }]) }),
      });
      await renderQueue();
      expect(editor()).toHaveValue("她有三只猫。");
      expect(screen.getByRole("radio", { name: "Custom reason" })).toBeChecked();
      expect(screen.getByPlaceholderText(/Describe the source-grounded reason/)).toHaveValue("Checked with the author.");
      expect(decision("Replace with edit")).toHaveAttribute("aria-pressed", "true");
      expect(header().getByText(/decided/)).toHaveTextContent("1/1 decided");
    });
  });

  describe("findings and versions", () => {
    it("lists each distinct finding with its severity, category, origin, and quotes", async () => {
      reviewApi();
      await renderQueue();
      const findings = sectionOf(/^Findings \(2\)/);
      const [first, second] = [...findings.querySelectorAll(".finding")] as HTMLElement[];
      expect(first).toHaveTextContent("high numbers auditThe number changed.EN: threeZH: 四");
      expect(within(first).getByText("EN: three")).toHaveAttribute("lang", "en");
      expect(within(first).getByText("ZH: 四")).toHaveAttribute("lang", "zh-CN");
      expect(second).toHaveTextContent(/^\s*Reads stiffly\.$/);
    });

    it("highlights a finding's quotes in the source and the translation, and clears them on a second click", async () => {
      reviewApi();
      const user = await renderQueue();
      const marks = () => [...document.querySelectorAll("mark")].map((m) => m.textContent);
      expect(marks()).toEqual([]);

      await user.click(screen.getByText("The number changed."));
      expect(marks()).toEqual(["three", "四"]);

      await user.click(screen.getByText("Reads stiffly.")); // has no quotes
      expect(marks()).toEqual([]);

      await user.click(screen.getByText("The number changed."));
      await user.click(screen.getByText("The number changed."));
      expect(marks()).toEqual([]);
    });

    it("still lists what was flagged for a passage with no further detail", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.click(sidebar().getByRole("button", { name: /D0001-S000001/ }));
      expect(sectionOf(/^Findings \(1\)/)).toHaveTextContent("D0001-S000001 was flagged");
      expect(within(sectionOf("Pipeline versions")).getByText("No alternative versions recorded.")).toBeInTheDocument();
    });

    it("loads an earlier pipeline version into the editor", async () => {
      reviewApi();
      const user = await renderQueue();
      const versions = within(sectionOf("Pipeline versions"));
      expect(versions.getAllByRole("button", { name: "Load into editor" })).toHaveLength(2); // the empty reprose version is left out
      expect(versions.getByText("repair")).toBeInTheDocument();

      await user.click(versions.getAllByRole("button", { name: "Load into editor" })[1]);
      expect(editor()).toHaveValue("她有三只猫咪。");
      expect(decision("Replace with edit")).toHaveAttribute("aria-pressed", "true");
    });
  });

  describe("editing and deciding", () => {
    it("turns an edit into a replacement and shows what changed", async () => {
      reviewApi();
      const user = await renderQueue();
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "true");

      await user.clear(editor());
      await user.type(editor(), "她有三只猫。");
      expect(decision("Replace with edit")).toHaveAttribute("aria-pressed", "true");
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "false");
      const diff = (screen.getByText("Changes vs current").nextElementSibling as HTMLElement);
      expect([...diff.querySelectorAll("del")].map((d) => d.textContent)).toEqual(["四"]);
      expect([...diff.querySelectorAll("ins")].map((i) => i.textContent)).toEqual(["三"]);
      expect(sectionOf("Your translation")).toHaveTextContent("6 chars (current 6)");
    });

    it("goes back to pending when the edit is reset", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      expect(decision("Replace with edit")).toHaveAttribute("aria-pressed", "true");

      await user.click(screen.getByRole("button", { name: "Reset to current" }));
      expect(editor()).toHaveValue("她有四只猫。");
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "true");
      expect(screen.queryByText("Changes vs current")).not.toBeInTheDocument();
    });

    it("warns about an empty translation, leftover English, changed digits, and a missing reason", async () => {
      reviewApi({ [`GET ${BASE}`]: review({ worksheet: worksheet([resolution("D0000-S000001", "Room 12.", "12号房间。")]) }) });
      const user = await renderQueue();
      const lint = () => (document.querySelector(".lint") as HTMLElement).textContent;
      expect(lint()).toBe("");

      await user.clear(editor());
      expect(lint()).toBe("translation is empty · digits changed from current · reason required");

      await user.type(editor(), "Room 12 号 room");
      expect(lint()).toBe("English left in text: Room, room · reason required");

      await user.click(screen.getByRole("radio", { name: REPLACE_REASON }));
      expect(lint()).toBe("English left in text: Room, room");
    });

    it("needs a reason before the current translation can be accepted", async () => {
      reviewApi();
      const user = await renderQueue();
      expect(decision("Accept current")).toBeDisabled();
      expect(decision("Accept current")).toHaveAttribute("title", "Select or enter a reason first");
      expect(screen.getByText("Select a reason to enable Accept.")).toBeInTheDocument();

      await user.click(screen.getByRole("radio", { name: ACCEPT_REASON }));
      expect(screen.queryByText("Select a reason to enable Accept.")).not.toBeInTheDocument();
      await user.click(decision("Accept current"));

      expect(decision("Accept current")).toHaveAttribute("aria-pressed", "true");
      expect(sidebar().getByText("Resolved (2)")).toBeInTheDocument();
      expect(sidebar().getByText("To review (1)")).toBeInTheDocument();
      expect(header().getByText(/decided/)).toHaveTextContent("2/3 decided");
    });

    it("accepts a custom reason of at least three characters", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      const custom = screen.getByPlaceholderText(/Describe the source-grounded reason/);
      await user.type(custom, "ok");
      expect(screen.getByRole("radio", { name: "Custom reason" })).toBeChecked();
      expect(decision("Accept current")).toBeDisabled();

      await user.type(custom, "ay per the author");
      await user.click(decision("Accept current"));
      await user.click(header().getByRole("button", { name: /Save draft/ }));
      await waitFor(() => expect(savedSheets(api)).toHaveLength(1));
      expect(savedSheets(api)[0].worksheet.resolutions[0]).toMatchObject({ decision: "accept", reason: "okay per the author", translated_text: null });
    });

    it("drops an acceptance whose reason is taken away", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.click(sidebar().getByRole("button", { name: /D0000-S000002/ })); // accepted with a preset reason
      expect(decision("Accept current")).toHaveAttribute("aria-pressed", "true");

      await user.click(screen.getByRole("radio", { name: "Custom reason" })); // an empty custom reason
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "true");
      expect(sidebar().getByText("To review (3)")).toBeInTheDocument();
      expect(sidebar().queryByText(/^Resolved/)).not.toBeInTheDocument();
    });

    it("asks before accepting over an edit, and discards the edit on confirm", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("radio", { name: ACCEPT_REASON }));
      await user.click(decision("Accept current"));

      const dialog = await screen.findByRole("alertdialog");
      expect(dialog).toHaveAccessibleName("Discard your edit?");
      expect(dialog).toHaveTextContent("Accepting keeps the current translation and discards your edit.");
      await user.click(within(dialog).getByRole("button", { name: "Accept current" }));

      await waitFor(() => expect(editor()).toHaveValue("她有四只猫。"));
      expect(decision("Accept current")).toHaveAttribute("aria-pressed", "true");
    });

    it("keeps the edit when that confirmation is cancelled", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("radio", { name: ACCEPT_REASON }));
      await user.click(decision("Accept current"));
      await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Cancel" }));

      await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
      expect(editor()).toHaveValue("她有四只猫。！");
      expect(decision("Replace with edit")).toHaveAttribute("aria-pressed", "true");
    });

    it("will not replace with an unchanged text", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.click(decision("Replace with edit"));
      expect(await screen.findByRole("status")).toHaveTextContent("Edit the translation first: replace needs a changed text.");
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "true");
    });

    it("can put a decided segment back to pending", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.click(sidebar().getByRole("button", { name: /D0000-S000002/ }));
      await user.click(decision("Pending"));
      expect(decision("Pending")).toHaveAttribute("aria-pressed", "true");
      expect(header().getByText(/decided/)).toHaveTextContent("0/3 decided");
    });
  });

  describe("deterministic check", () => {
    it("checks the decided text on request and says when it passes", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.clear(editor());
      await user.type(editor(), "她有三只猫。");
      await user.click(screen.getByRole("button", { name: "Check" }));

      expect(await screen.findByText("Passes deterministic validation.")).toBeInTheDocument();
      expect(api.posted(`${BASE}/check`)).toEqual([{ segment_id: "D0000-S000001", decision: "replace", text: "她有三只猫。" }]);
    });

    it("lists what would be refused", async () => {
      reviewApi({ [`POST ${BASE}/check`]: BLOCKING });
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText(/Would be refused by deterministic validation:/)).toHaveTextContent("structure (high): protected marker removed");
    });

    it("checks by itself shortly after the text settles", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      expect(screen.queryByText("Passes deterministic validation.")).not.toBeInTheDocument();
      expect(await screen.findByText("Passes deterministic validation.", {}, { timeout: 3000 })).toBeInTheDocument();
      expect(api.posted(`${BASE}/check`)).toEqual([{ segment_id: "D0000-S000001", decision: "replace", text: "她有四只猫。！" }]);
    });

    it("does not check a segment that is still pending", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(api.posted(`${BASE}/check`)).toEqual([]);
      expect(screen.queryByText(/deterministic validation/)).not.toBeInTheDocument();
    });

    it("does not ask again for a text it has already checked", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("button", { name: "Check" }));
      await screen.findByText("Passes deterministic validation.");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(api.posted(`${BASE}/check`)).toHaveLength(1);
    });

    it("drops a stale verdict as soon as the text changes", async () => {
      reviewApi();
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("button", { name: "Check" }));
      await screen.findByText("Passes deterministic validation.");

      await user.type(editor(), "？");
      expect(screen.queryByText("Passes deterministic validation.")).not.toBeInTheDocument();
    });

    it("treats a check that could not run as blocking, with the reason", async () => {
      reviewApi({ [`POST ${BASE}/check`]: apiError("audit module crashed", 500) });
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(screen.getByRole("button", { name: "Check" }));
      expect(await screen.findByText(/Would be refused by deterministic validation:/)).toHaveTextContent("check (error): audit module crashed");
    });
  });

  describe("saving a draft", () => {
    it("saves every decision as it stands and clears the unsaved mark", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      expect(header().getByRole("button", { name: "Save draft" })).toHaveAttribute("title", "Ctrl+S");

      await user.clear(editor());
      await user.type(editor(), "她有三只猫。");
      await user.click(screen.getByRole("radio", { name: REPLACE_REASON }));
      await user.click(header().getByRole("button", { name: "Save draft •" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Draft saved.");
      expect(header().getByRole("button", { name: "Save draft" })).toBeInTheDocument();
      const [{ worksheet: sheet }] = savedSheets(api);
      expect(sheet).toMatchObject({ schema_version: 1, workspace: "runs/demo", draft_output_hash: "abc123" });
      expect(sheet.resolutions).toEqual([
        { ...CATS, decision: "replace", translated_text: "她有三只猫。", reason: REPLACE_REASON },
        { ...HELLO, translated_text: null },
        { ...BYE, translated_text: null },
      ]);
    });

    it("saves on Ctrl+S", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.keyboard("{Control>}s{/Control}");
      expect(await screen.findByRole("status")).toHaveTextContent("Draft saved.");
      expect(savedSheets(api)).toHaveLength(1);
    });

    it("says when the save failed and keeps the unsaved mark", async () => {
      reviewApi({ [`POST ${BASE}/save`]: apiError("disk full", 500) });
      const user = await renderQueue();
      await user.type(editor(), "！");
      await user.click(header().getByRole("button", { name: "Save draft •" }));
      expect(await screen.findByRole("status")).toHaveTextContent("Save failed: disk full");
      expect(header().getByRole("button", { name: "Save draft •" })).toBeInTheDocument();
    });
  });

  describe("applying", () => {
    const decided = () => review({ worksheet: worksheet([accepted(CATS), HELLO, accepted(BYE)]) });
    const applied = (api: ReturnType<typeof reviewApi>) => api.posted(`${BASE}/apply`) as { worksheet: ReturnType<typeof worksheet>; approve_final: boolean; partial: boolean }[];

    it("checks every segment, applies the decisions, and approves the final draft", async () => {
      const api = reviewApi({ [`GET ${BASE}`]: decided() });
      const user = await renderQueue();
      expect(header().getByRole("checkbox", { name: "approve final draft" })).toBeChecked();
      await user.click(header().getByRole("button", { name: "Apply decisions" }));

      expect(await screen.findByText("All decisions applied. Final draft approved. Nothing left in the review queue.")).toBeInTheDocument();
      expect(api.posted(`${BASE}/check`)).toHaveLength(3);
      const [body] = applied(api);
      expect(body).toMatchObject({ approve_final: true, partial: false });
      expect(body.worksheet.resolutions.map((r) => r.decision)).toEqual(["accept", "accept", "accept"]);
      // The queue is gone; what remains is the way on to the EPUB.
      expect(screen.queryByRole("button", { name: "Apply decisions" })).not.toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Compile now" })).toBeInTheDocument();
      expect(api.count(BASE)).toBe(2);
    });

    it("applies without approving when the box is unticked, and names what still needs review", async () => {
      const api = reviewApi({
        [`GET ${BASE}`]: decided(),
        [`POST ${BASE}/apply`]: { report: { passed: false, review_segment_ids: ["D0000-S000001"] }, approved: false },
      });
      const user = await renderQueue();
      await user.click(header().getByRole("checkbox", { name: "approve final draft" }));
      await user.click(header().getByRole("button", { name: "Apply decisions" }));

      expect(await screen.findByText("Applied, but 1 segment(s) still need review: D0000-S000001. Compile to regenerate the worksheet.")).toBeInTheDocument();
      expect(applied(api)[0]).toMatchObject({ approve_final: false, partial: false });
    });

    it("says nothing about approval when everything passed without it", async () => {
      reviewApi({ [`GET ${BASE}`]: decided(), [`POST ${BASE}/apply`]: { report: { passed: true, review_segment_ids: [] }, approved: false } });
      const user = await renderQueue();
      await user.click(header().getByRole("button", { name: "Apply decisions" }));
      expect(await screen.findByText("All decisions applied. Nothing left in the review queue.")).toBeInTheDocument();
    });

    it("refuses while segments are undecided, and jumps to the first of them", async () => {
      const api = reviewApi();
      const user = await renderQueue();
      await user.click(sidebar().getByRole("button", { name: /D0000-S000002/ }));
      await user.click(header().getByRole("button", { name: "Apply decisions" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Decide every segment with a reason first (2 left).");
      expect(title()).toBe("D0000-S000001");
      expect(applied(api)).toEqual([]);
      expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
    });

    it("stops at a segment that would fail validation, and applies nothing", async () => {
      const api = reviewApi({
        [`GET ${BASE}`]: decided(),
        [`POST ${BASE}/check`]: (body: { segment_id: string }) => (body.segment_id === "D0001-S000001" ? BLOCKING : { blocking: [] }),
      });
      const user = await renderQueue();
      await user.click(header().getByRole("button", { name: "Apply decisions" }));

      expect(await screen.findByRole("status")).toHaveTextContent("D0001-S000001 would fail deterministic validation; see the red box under the editor.");
      expect(title()).toBe("D0001-S000001");
      expect(screen.getByText(/Would be refused by deterministic validation:/)).toHaveTextContent("protected marker removed");
      expect(applied(api)).toEqual([]);
      expect(header().getByRole("button", { name: "Apply decisions" })).toBeEnabled();
    });

    it("says nothing was changed when the server refuses to apply", async () => {
      reviewApi({ [`GET ${BASE}`]: decided(), [`POST ${BASE}/apply`]: apiError("the draft changed since this worksheet was written", 409) });
      const user = await renderQueue();
      await user.click(header().getByRole("button", { name: "Apply decisions" }));

      const toast = await screen.findByRole("status");
      expect(toast).toHaveTextContent("Apply refused; nothing was changed.");
      expect(toast).toHaveTextContent("the draft changed since this worksheet was written");
      expect(screen.getByRole("heading", { name: "Your translation" })).toBeInTheDocument(); // still in the queue
      expect(header().getByRole("button", { name: "Apply decisions" })).toBeEnabled();
    });

    describe("within the compile limit", () => {
      const limited = (limit: number) => reviewApi({
        [`GET ${BASE}`]: review({ compile_limit: limit }),
        [`POST ${BASE}/apply`]: { report: { passed: false, review_segment_ids: ["D0000-S000001", "D0001-S000001"] }, approved: true },
      });

      it("offers to apply what is decided and approve, leaving the rest unresolved", async () => {
        const api = limited(2);
        const user = await renderQueue();
        await user.click(header().getByRole("button", { name: "Apply decisions" }));

        const dialog = await screen.findByRole("alertdialog");
        expect(dialog).toHaveAccessibleName("Within the compile limit");
        expect(dialog).toHaveTextContent("2 segments are still undecided, and this job's compile limit allows 2 unresolved.");
        expect(dialog).toHaveTextContent("apply your 1 decision now and approve the final draft");
        expect(within(dialog).getByRole("button", { name: "Continue resolving" })).toHaveFocus();

        await user.click(within(dialog).getByRole("button", { name: "Apply & approve now" }));
        expect(await screen.findByText(
          "Decisions applied and final draft approved. 2 segment(s) stay unresolved within the compile limit: D0000-S000001, D0001-S000001.",
        )).toBeInTheDocument();
        const [body] = applied(api);
        expect(body).toMatchObject({ approve_final: true, partial: true });
        expect(body.worksheet.resolutions.map((r) => r.decision)).toEqual(["pending", "accept", "pending"]);
        expect(api.posted(`${BASE}/check`)).toEqual([expect.objectContaining({ segment_id: "D0000-S000002" })]); // only the decided one
      });

      it("approves a partial apply even when the approve box is unticked", async () => {
        const api = limited(2);
        const user = await renderQueue();
        await user.click(header().getByRole("checkbox", { name: "approve final draft" }));
        await user.click(header().getByRole("button", { name: "Apply decisions" }));
        await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Apply & approve now" }));
        await waitFor(() => expect(applied(api)).toHaveLength(1));
        expect(applied(api)[0]).toMatchObject({ approve_final: true, partial: true });
      });

      it("keeps resolving when the user prefers to, and reminds them of the limit", async () => {
        const api = limited(2);
        const user = await renderQueue();
        await user.click(header().getByRole("button", { name: "Apply decisions" }));
        await user.click(within(await screen.findByRole("alertdialog")).getByRole("button", { name: "Continue resolving" }));

        expect(await screen.findByRole("status")).toHaveTextContent("Decide every segment with a reason first (2 left; the compile limit allows 2).");
        expect(applied(api)).toEqual([]);
        expect(screen.getByRole("heading", { name: "Your translation" })).toBeInTheDocument();
      });

      it("offers it unasked the moment the undecided count drops into the limit", async () => {
        const api = limited(1);
        const user = await renderQueue();
        expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument(); // 2 undecided: over the limit

        await user.click(screen.getByRole("radio", { name: ACCEPT_REASON }));
        await user.click(decision("Accept current"));
        const dialog = await screen.findByRole("alertdialog");
        expect(dialog).toHaveTextContent("1 segment is still undecided, and this job's compile limit allows 1 unresolved.");
        expect(dialog).toHaveTextContent("apply your 2 decisions now");

        await user.click(within(dialog).getByRole("button", { name: "Continue resolving" }));
        await waitFor(() => expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument());
        expect(applied(api)).toEqual([]);
      });
    });
  });

  describe("compiling", () => {
    const emptyQueue = (compile: unknown = IDLE) => review({ worksheet: null, compile });
    const DONE = {
      state: "done",
      events: [
        { stage: "compile", status: "completed", message: "wrote 12 documents" },
        { stage: "reprose_translation", status: "skipped", message: "" },
        { stage: "validate_epub", status: "completed", message: "" },
      ],
      result: { result: "complete", message: "all checks passed", output: "output/alice.zh.epub" },
    };

    it("starts the compile, then shows its stages and the result", async () => {
      const api = reviewApi({ [`GET ${BASE}`]: emptyQueue(), [`GET ${BASE}/compile`]: DONE });
      const user = await renderReviewTab();
      await user.click(await screen.findByRole("button", { name: "Compile now" }));

      expect(await screen.findByRole("status")).toHaveTextContent("Compile finished: complete");
      expect(api.posted(`${BASE}/compile`)).toEqual([{}]);
      const log = document.querySelector(".compile-log") as HTMLElement;
      expect(log).toHaveTextContent("wrote 12 documents");
      expect(log).toHaveTextContent(/validate_epub\s+completed/);
      expect(log).not.toHaveTextContent("reprose_translation"); // skipped stages are left out
      expect(log).toHaveTextContent("→ complete: all checks passed");
      expect(log).toHaveTextContent("EPUB: output/alice.zh.epub");
    });

    it("follows a compile that is already running until it ends", async () => {
      let polls = 0;
      reviewApi({
        [`GET ${BASE}`]: emptyQueue({ state: "running", events: [{ stage: "compile", status: "running", message: "" }], result: null }),
        [`GET ${BASE}/compile`]: () => {
          polls += 1;
          return polls < 2
            ? { state: "running", events: [{ stage: "compile", status: "running", message: "" }], result: null }
            : { state: "done", events: [], result: { result: "paused", message: "3 segments require review" } };
        },
      });
      await renderReviewTab();
      const button = await screen.findByRole("button", { name: "Compile now" });
      await waitFor(() => expect(button).toBeDisabled());

      expect(await screen.findByRole("status", {}, { timeout: 4000 })).toHaveTextContent("Compile finished: paused");
      expect(document.querySelector(".compile-log")).toHaveTextContent("→ paused: 3 segments require review");
      expect(button).toBeEnabled();
    });

    it("shows why the compile could not start", async () => {
      reviewApi({ [`GET ${BASE}`]: emptyQueue(), [`POST ${BASE}/compile`]: apiError("the job is running", 409) });
      const user = await renderReviewTab();
      await user.click(await screen.findByRole("button", { name: "Compile now" }));
      expect(await screen.findByRole("status")).toHaveTextContent("the job is running");
      expect(document.querySelector(".compile-log")).toBeNull();
    });
  });
});

describe("Final review tab: the book's languages", () => {
  const lint = () => (document.querySelector(".lint") as HTMLElement).textContent;
  const quotes = () => [...document.querySelectorAll(".finding .quote")].map((q) => [q.textContent, q.getAttribute("lang")]);

  /** One flagged passage of a book translated between two languages. */
  function passage(job: unknown, source_text: string, current_translation: string, source_quote: string, translation_quote: string) {
    const flagged = resolution("D0000-S000001", source_text, current_translation);
    return reviewApi({
      "GET /api/jobs/demo/info": job,
      [`GET ${BASE}`]: review({
        worksheet: worksheet([flagged]),
        context: { "D0000-S000001": { findings: [{ message: "The meaning shifted.", source_quote, translation_quote }] } },
      }),
    });
  }

  const ZH = language("zh", "Simplified Chinese");
  const EN = language("en", "English");
  const AH_Q = "阿Q没有家，住在未庄的土谷祠里。";

  it("Chinese into English: labels the quotes ZH and EN, and warns only about Chinese left behind", async () => {
    passage(jobIn("zh-en", ZH, EN), AH_Q, "Ah Q had no family; he lived in the Tutelary God's Temple at Weizhuang.", "土谷祠", "Tutelary God's Temple");
    const user = await renderQueue();
    expect(quotes()).toEqual([["ZH: 土谷祠", "zh-CN"], ["EN: Tutelary God's Temple", "en"]]);
    expect(sectionOf("Source").querySelector(".text")).toHaveAttribute("lang", "zh-CN");
    expect(editor()).toHaveAttribute("lang", "en");

    await user.type(editor(), " He was homeless.");
    expect(lint()).toBe("reason required"); // English in an English translation is the point

    await user.clear(editor());
    await user.type(editor(), "Ah Q lived in the 土谷祠 at Weizhuang.");
    expect(lint()).toBe("Simplified Chinese left in text: 土谷祠 · reason required");
    expect(screen.getByText("Changes vs current").nextElementSibling).toHaveAttribute("lang", "en");
  });

  it("English into German: never mistakes German for English left behind", async () => {
    passage(
      jobIn("en>de", EN, language("de", "German", "profiled")),
      "Alice was beginning to get very tired of sitting by her sister on the bank.",
      "Alice fing an, sich zu langweilen; sie saß schon lange bei ihrer Schwester am Ufer.",
      "very tired", "sich zu langweilen",
    );
    const user = await renderQueue();
    expect(quotes()).toEqual([["EN: very tired", "en"], ["DE: sich zu langweilen", "de"]]);
    expect(editor()).toHaveAttribute("lang", "de");

    await user.type(editor(), " Sie hatte nichts zu tun.");
    expect(lint()).toBe("reason required");
  });

  it("Chinese into Japanese: characters the two languages share are not flagged", async () => {
    passage(jobIn("zh>ja", ZH, language("ja", "Japanese", "profiled")), AH_Q, "阿Qには家がなく、未荘の土穀祠に住んでいた。", "土谷祠", "土穀祠");
    const user = await renderQueue();
    expect(quotes()).toEqual([["ZH: 土谷祠", "zh-CN"], ["JA: 土穀祠", "ja"]]);
    await user.type(editor(), "彼は独りだった。");
    expect(lint()).toBe("reason required");
  });

  it("Japanese into Chinese: kana left behind is flagged", async () => {
    passage(jobIn("ja>zh", language("ja", "Japanese", "profiled"), ZH), "吾輩は猫である。名前はまだ無い。", "我是猫。名字还没有。", "まだ無い", "还没有");
    const user = await renderQueue();
    await user.type(editor(), "まだ");
    expect(lint()).toBe("Japanese left in text: まだ · reason required");
  });

  it("French into English: accented letters are not flagged either", async () => {
    passage(
      jobIn("fr>en", language("fr", "French", "generic"), EN),
      "Longtemps, je me suis couché de bonne heure.", "For a long time I used to go to bed early.", "de bonne heure", "early",
    );
    const user = await renderQueue();
    expect(quotes()).toEqual([["FR: de bonne heure", "fr"], ["EN: early", "en"]]);
    await user.type(editor(), " Déjà vu.");
    expect(lint()).toBe("reason required");
  });

  it("works from the direction alone when an older server does not describe the languages", async () => {
    passage(jobInfo({ overall: "paused", stages: PAUSED, direction: "zh-en" }), AH_Q, "Ah Q had no family.", "阿Q", "Ah Q");
    const user = await renderQueue();
    expect(quotes()).toEqual([["ZH: 阿Q", "zh-CN"], ["EN: Ah Q", "en"]]);
    await user.type(editor(), " 未庄");
    expect(lint()).toBe("ZH left in text: 未庄 · reason required");
  });

  it("assumes English into Chinese when the server says nothing about the languages", async () => {
    passage(jobInfo({ overall: "paused", stages: PAUSED }), "She has three cats.", "她有四只猫。", "three", "四");
    await renderQueue();
    expect(quotes()).toEqual([["EN: three", "en"], ["ZH: 四", "zh-CN"]]);
  });
});

describe("Final review tab: when the server stumbles", () => {
  it("keeps following a compile when one status check fails, and says so", async () => {
    let polls = 0;
    reviewApi({
      [`GET ${BASE}`]: review({ worksheet: null, compile: { state: "running", events: [], result: null } }),
      [`GET ${BASE}/compile`]: () => {
        polls += 1;
        return polls === 1
          ? apiError("server restarting", 503)
          : { state: "done", events: [], result: { result: "complete", output: "output/alice.zh.epub" } };
      },
    });
    await renderReviewTab();
    const button = await screen.findByRole("button", { name: "Compile now" });
    expect(await screen.findByRole("status")).toHaveTextContent("Could not check the compile: server restarting");

    expect(await screen.findByText("Compile finished: complete", {}, { timeout: 4000 })).toBeInTheDocument();
    expect(document.querySelector(".compile-log")).toHaveTextContent("EPUB: output/alice.zh.epub");
    expect(button).toBeEnabled();
  });

  it("shows the next job's queue, not the error left by the job before it", async () => {
    reviewApi({
      [`GET ${BASE}`]: apiError("worksheet is not valid JSON", 500),
      "GET /api/jobs/sign/info": jobInfo({ job_id: "sign", overall: "paused", stages: PAUSED }),
      "GET /api/jobs/sign/review": review(),
    });
    await renderReviewTab();
    expect(await screen.findByText("worksheet is not valid JSON")).toBeInTheDocument();

    // The browser's Back button lands on another job's review without a page load.
    act(() => {
      window.history.pushState({}, "", "/jobs/sign/review");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(await screen.findByRole("heading", { name: "Your translation" })).toBeInTheDocument();
    expect(screen.queryByText("worksheet is not valid JSON")).not.toBeInTheDocument();
  });
});

// -- journeys -------------------------------------------------------------------
// README: "Each segment shows the source with neighboring context, the findings (click
// one to highlight the quoted words), earlier pipeline versions, and an editor with a
// live diff and the same deterministic check ... Applying the decisions approves the
// draft; the desk then compiles and validates the EPUB."

describe("Final review tab: a reviewer works through the queue", () => {
  const lint = () => (document.querySelector(".lint") as HTMLElement).textContent;
  const TEA = resolution("D0006-S000041", "“Have some wine,” the March Hare said in an encouraging tone.", "“喝点酒吧，”三月兔用鼓励的口气说。");
  const HEADS = resolution("D0007-S000112", "The Queen had only one way of settling all difficulties, great or small.", "王后解决一切困难的办法只有一个，无论大小。");
  const PLAYERS = resolution("D0007-S000118", "All the players, except the King, the Queen, and Alice, were in custody.", "除了国王、王后和爱丽丝，其余2个打球的人都被关了起来。");
  const ALICE_CONTEXT = {
    "D0007-S000118": {
      chapter_title: "The Queen's Croquet-Ground",
      review_kind: "audit",
      previous_source_context: "By the end of half an hour or so, there were no arches left.",
      findings: [{ message: "The translation adds a number the source does not have.", severity: "high", category: "numbers", origin: "audit", source_quote: "All the players", translation_quote: "其余2个打球的人" }],
      translation_versions: [{ stage: "translate", text: "除了国王、王后和爱丽丝，所有打球的人都被关了起来。" }],
    },
    "D0006-S000041": { chapter_title: "A Mad Tea-Party" },
    "D0007-S000112": { chapter_title: "The Queen's Croquet-Ground" },
  };

  /** Alice's queue on a pretend server that remembers what was applied and compiled. */
  function aliceQueue(overrides: Record<string, unknown> = {}, resolutions = [PLAYERS, TEA, HEADS]) {
    const server = { applied: false, compiling: false };
    const api = reviewApi({
      [`GET ${BASE}`]: () => review({ worksheet: worksheet(resolutions), context: ALICE_CONTEXT, stale: server.applied, ...overrides }),
      [`POST ${BASE}/check`]: (body: { text: string; decision: string }) =>
        body.decision === "replace" && /\d/.test(body.text)
          ? { blocking: [{ category: "numbers", severity: "high", message: "the translation has a number the source does not" }] }
          : { blocking: [] },
      [`POST ${BASE}/apply`]: () => { server.applied = true; return { report: { passed: true, review_segment_ids: [] }, approved: true }; },
      [`POST ${BASE}/compile`]: () => { server.compiling = true; return { started: true }; },
      [`GET ${BASE}/compile`]: () => ({
        state: "done",
        events: [{ stage: "compile", status: "completed", message: "" }, { stage: "validate_epub", status: "completed", message: "" }],
        result: { result: "complete", output: "output/alice.zh.epub" },
      }),
    });
    return { api, server };
  }
  const accept = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole("radio", { name: ACCEPT_REASON }));
    await user.click(decision("Accept current"));
  };

  it("rewrites a flagged passage, is told a number is still wrong, fixes it, settles the rest, and compiles the book", async () => {
    const { api } = aliceQueue();
    const user = await renderQueue();

    // The passage, where it is in the book, and what the audit objected to.
    expect(title()).toBe("D0007-S000118");
    expect(document.querySelector(".seghead")).toHaveTextContent("The Queen's Croquet-Ground");
    expect(sectionOf("Source")).toHaveTextContent("By the end of half an hour or so, there were no arches left.");
    await user.click(screen.getByText("The translation adds a number the source does not have."));
    expect([...document.querySelectorAll("mark")].map((m) => m.textContent)).toEqual(["All the players", "其余2个打球的人"]);

    // A first rewrite keeps a digit: the page says so before anything is applied.
    await user.clear(editor());
    await user.type(editor(), "除了国王、王后和爱丽丝，所有3个打球的人都被关了起来。");
    expect(lint()).toContain("digits changed from current");
    await user.click(screen.getByRole("radio", { name: REPLACE_REASON }));
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText(/Would be refused by deterministic validation:/)).toHaveTextContent("the translation has a number the source does not");

    // The earlier pipeline version was right all along: take it.
    await user.click(within(sectionOf("Pipeline versions")).getByRole("button", { name: "Load into editor" }));
    expect(editor()).toHaveValue("除了国王、王后和爱丽丝，所有打球的人都被关了起来。");
    await user.click(screen.getByRole("button", { name: "Check" }));
    expect(await screen.findByText("Passes deterministic validation.")).toBeInTheDocument();
    expect(screen.getByText("Changes vs current").nextElementSibling).toHaveTextContent("除了国王、王后和爱丽丝，其余2个所有打球的人都被关了起来。");

    // The other two were false alarms.
    await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
    expect(title()).toBe("D0006-S000041");
    await accept(user);
    await user.keyboard("{Alt>}{ArrowDown}{/Alt}");
    expect(title()).toBe("D0007-S000112");
    await accept(user);
    expect(header().getByText(/decided/)).toHaveTextContent("3/3 decided");

    await user.click(header().getByRole("button", { name: "Apply decisions" }));
    expect(await screen.findByText("All decisions applied. Final draft approved. Nothing left in the review queue.")).toBeInTheDocument();
    const [applied] = api.posted(`${BASE}/apply`) as { worksheet: ReturnType<typeof worksheet> }[];
    expect(applied.worksheet.resolutions.map((r) => [r.segment_id, r.decision, r.translated_text])).toEqual([
      ["D0007-S000118", "replace", "除了国王、王后和爱丽丝，所有打球的人都被关了起来。"],
      ["D0006-S000041", "accept", null],
      ["D0007-S000112", "accept", null],
    ]);

    await user.click(screen.getByRole("button", { name: "Compile now" }));
    expect(await screen.findByText("Compile finished: complete")).toBeInTheDocument();
    expect(document.querySelector(".compile-log")).toHaveTextContent("EPUB: output/alice.zh.epub");
  });

  it("decides half the queue before lunch, saves a draft, and finds the decisions there on return", async () => {
    let saved: ReturnType<typeof worksheet> | null = null;
    const api = reviewApi({
      [`GET ${BASE}`]: () => review({ worksheet: saved ?? worksheet([PLAYERS, TEA, HEADS]), context: ALICE_CONTEXT }),
      [`POST ${BASE}/save`]: (body: { worksheet: ReturnType<typeof worksheet> }) => { saved = body.worksheet; return { saved: true }; },
    });
    let user = await renderQueue();
    await user.click(sidebar().getByRole("button", { name: /D0006-S000041/ }));
    await user.type(screen.getByPlaceholderText(/Describe the source-grounded reason/), "The Hare offers wine that is not there; the Chinese says the same.");
    await user.click(decision("Accept current"));
    await user.keyboard("{Control>}s{/Control}");
    expect(await screen.findByRole("status")).toHaveTextContent("Draft saved.");
    cleanup();

    user = await renderQueue();
    expect(sidebar().getByText("Resolved (1)")).toBeInTheDocument();
    expect(sidebar().getByText("To review (2)")).toBeInTheDocument();
    await user.click(sidebar().getByRole("button", { name: /D0006-S000041/ }));
    expect(screen.getByRole("radio", { name: "Custom reason" })).toBeChecked();
    expect(screen.getByPlaceholderText(/Describe the source-grounded reason/)).toHaveValue("The Hare offers wine that is not there; the Chinese says the same.");
    expect(decision("Accept current")).toHaveAttribute("aria-pressed", "true");
    expect(api.posted(`${BASE}/save`)).toHaveLength(1);
  });

  it("has two hard passages left that the job is allowed to ship with, and approves the book without them", async () => {
    const { api } = aliceQueue({ compile_limit: 2 });
    const user = await renderQueue();
    await user.click(sidebar().getByRole("button", { name: /D0006-S000041/ }));
    await accept(user);

    // Deciding one brings the queue within the limit: the page offers the way out by itself.
    const dialog = await screen.findByRole("alertdialog");
    expect(dialog).toHaveTextContent("2 segments are still undecided, and this job's compile limit allows 2 unresolved.");
    expect(dialog).toHaveTextContent("Undecided segments keep their current translation and stay listed in the review report.");
    await user.click(within(dialog).getByRole("button", { name: "Apply & approve now" }));

    await waitFor(() => expect(api.posted(`${BASE}/apply`)).toHaveLength(1));
    expect(api.posted(`${BASE}/apply`)[0]).toMatchObject({ partial: true, approve_final: true });
    expect(await screen.findByRole("button", { name: "Compile now" })).toBeInTheDocument();
  });

  it("reviews The Sign of the Four going into German: the passage, the labels, and the warnings are about German", async () => {
    const WATSON = resolution(
      "D0001-S000007",
      "Sherlock Holmes took his bottle from the corner of the mantelpiece.",
      "Sherlock Holmes nahm seine Flasche von der Ecke des Kaminsimses.",
    );
    const api = reviewApi({
      "GET /api/jobs/demo/info": jobIn("en>de", language("en", "English"), language("de", "German", "profiled")),
      [`GET ${BASE}`]: review({
        worksheet: worksheet([WATSON]),
        context: { "D0001-S000007": { chapter_title: "The Science of Deduction", findings: [{ message: "“bottle” may be a vial here.", source_quote: "his bottle", translation_quote: "seine Flasche" }] } },
      }),
    });
    const user = await renderQueue();
    expect(screen.getByRole("banner")).toHaveTextContent("EN → DE"); // the header says which languages this job is in
    expect([...document.querySelectorAll(".finding .quote")].map((q) => q.textContent)).toEqual(["EN: his bottle", "DE: seine Flasche"]);

    await user.clear(editor());
    await user.type(editor(), "Sherlock Holmes nahm sein Fläschchen von der Ecke des Kaminsimses.");
    expect(lint()).toBe("reason required"); // German is not "English left in text"
    expect(editor()).toHaveAttribute("lang", "de");
    await user.click(screen.getByRole("radio", { name: REPLACE_REASON }));
    await user.click(header().getByRole("button", { name: "Apply decisions" }));

    await waitFor(() => expect(api.posted(`${BASE}/apply`)).toHaveLength(1));
    expect((api.posted(`${BASE}/apply`)[0] as { worksheet: ReturnType<typeof worksheet> }).worksheet.resolutions[0]).toMatchObject({
      decision: "replace", translated_text: "Sherlock Holmes nahm sein Fläschchen von der Ecke des Kaminsimses.", reason: REPLACE_REASON,
    });
  });
});
