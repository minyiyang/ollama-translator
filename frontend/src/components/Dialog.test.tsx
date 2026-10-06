import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { DialogProvider, useConfirm, useDialog, type DialogAction } from "./Dialog";

const ACTIONS: DialogAction<"cancel" | "rerun">[] = [
  { value: "cancel", label: "Cancel", primary: true },
  { value: "rerun", label: "Rerun", danger: true },
];

/** Opens a dialog and prints what each one resolved to. */
function Asker({ onAnswer = () => {} }: { onAnswer?: (which: string, value: string | null) => void }) {
  const ask = useDialog();
  const [answers, setAnswers] = useState<string[]>([]);
  const open = (which: string) =>
    ask(`Rerun ${which}?`, <p>Finished work of {which} is discarded.</p>, ACTIONS).then((value) => {
      onAnswer(which, value);
      setAnswers((old) => [...old, `${which}=${value}`]);
    });
  return (
    <>
      <button onClick={() => open("translate")}>ask</button>
      <button onClick={() => { open("first"); open("second"); }}>ask twice</button>
      <p>answers: {answers.join(", ") || "none"}</p>
    </>
  );
}

function Confirmer({ label, danger }: { label?: string; danger?: boolean }) {
  const confirm = useConfirm();
  const [answer, setAnswer] = useState("none");
  return (
    <>
      <button onClick={() => confirm("Discard this job?", "The draft job is removed.", label, danger).then((ok) => setAnswer(String(ok)))}>confirm</button>
      <p>confirmed: {answer}</p>
    </>
  );
}

async function openDialog() {
  const user = userEvent.setup();
  render(<DialogProvider><Asker /></DialogProvider>);
  await user.click(screen.getByRole("button", { name: "ask" }));
  return { user, dialog: screen.getByRole("alertdialog") };
}

describe("A question the dashboard asks", () => {
  it("shows no dialog until one is asked for", () => {
    render(<DialogProvider><Asker /></DialogProvider>);
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("shows the question, its explanation, and one button per answer", async () => {
    const { dialog } = await openDialog();
    expect(dialog).toHaveAccessibleName("Rerun translate?");
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(within(dialog).getByText("Finished work of translate is discarded.")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Rerun"]);
  });

  it("starts with the suggested answer under the keyboard", async () => {
    const { dialog } = await openDialog();
    expect(within(dialog).getByRole("button", { name: "Cancel" })).toHaveFocus();
  });

  it("keeps Tab and Shift+Tab inside the dialog", async () => {
    const { user, dialog } = await openDialog();
    const [cancel, rerun] = within(dialog).getAllByRole("button");
    expect(cancel).toHaveFocus();
    await user.tab();
    expect(rerun).toHaveFocus();
    await user.tab();
    expect(cancel).toHaveFocus(); // wrapped round, not out to the page behind
    await user.tab({ shift: true });
    expect(rerun).toHaveFocus();
  });

  it("gives focus back to the button that opened it", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await screen.findByText("answers: translate=cancel");
    expect(screen.getByRole("button", { name: "ask" })).toHaveFocus();
  });

  it("gives focus back after Escape too", async () => {
    const { user } = await openDialog();
    await user.keyboard("{Escape}");
    await screen.findByText("answers: translate=null");
    expect(screen.getByRole("button", { name: "ask" })).toHaveFocus();
  });

  it("takes the answer the user clicks and closes", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByRole("button", { name: "Rerun" }));
    expect(await screen.findByText("answers: translate=rerun")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("takes the suggested answer on Enter", async () => {
    const { user } = await openDialog();
    await user.keyboard("{Enter}");
    expect(await screen.findByText("answers: translate=cancel")).toBeInTheDocument();
  });

  it("counts Escape as no answer", async () => {
    const { user } = await openDialog();
    await user.keyboard("{Escape}");
    expect(await screen.findByText("answers: translate=null")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("Escape closes only the question, not whatever the page behind would close", async () => {
    const pageHandler = vi.fn();
    window.addEventListener("keydown", pageHandler);
    try {
      const { user } = await openDialog();
      await user.keyboard("{Escape}");
      expect(pageHandler).not.toHaveBeenCalled();

      // With the dialog gone the page gets its keys back.
      await user.keyboard("{Escape}");
      expect(pageHandler).toHaveBeenCalledOnce();
    } finally {
      window.removeEventListener("keydown", pageHandler);
    }
  });

  it("leaves the page's other keyboard shortcuts working", async () => {
    const pageHandler = vi.fn();
    window.addEventListener("keydown", pageHandler);
    try {
      const { user } = await openDialog();
      await user.keyboard("a");
      expect(pageHandler).toHaveBeenCalledOnce();
      expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    } finally {
      window.removeEventListener("keydown", pageHandler);
    }
  });

  it("counts a click outside the box as no answer, and ignores a click on its text", async () => {
    const { user, dialog } = await openDialog();
    await user.click(within(dialog).getByText("Finished work of translate is discarded."));
    expect(screen.getByRole("alertdialog")).toBeInTheDocument();
    expect(screen.getByText("answers: none")).toBeInTheDocument();

    await user.click(dialog.parentElement as HTMLElement);
    expect(await screen.findByText("answers: translate=null")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });

  it("asks one thing at a time: a newer question replaces the older one, which goes unanswered", async () => {
    const user = userEvent.setup();
    render(<DialogProvider><Asker /></DialogProvider>);
    await user.click(screen.getByRole("button", { name: "ask twice" }));

    expect(screen.getAllByRole("alertdialog")).toHaveLength(1);
    expect(screen.getByRole("alertdialog")).toHaveAccessibleName("Rerun second?");
    expect(await screen.findByText("answers: first=null")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Rerun" }));
    expect(await screen.findByText("answers: first=null, second=rerun")).toBeInTheDocument();
  });

  it("takes one answer per question, however many keys follow", async () => {
    const onAnswer = vi.fn();
    const user = userEvent.setup();
    render(<DialogProvider><Asker onAnswer={onAnswer} /></DialogProvider>);
    await user.click(screen.getByRole("button", { name: "ask" }));
    await user.click(screen.getByRole("button", { name: "Rerun" }));
    await screen.findByText("answers: translate=rerun");
    await user.keyboard("{Escape}");
    expect(onAnswer).toHaveBeenCalledExactlyOnceWith("translate", "rerun");
  });
});

describe("A question asked outside the app shell", () => {
  it("goes unanswered without showing anything", async () => {
    render(<Asker />);
    await userEvent.setup().click(screen.getByRole("button", { name: "ask" }));
    expect(await screen.findByText("answers: translate=null")).toBeInTheDocument();
    expect(screen.queryByRole("alertdialog")).not.toBeInTheDocument();
  });
});

describe("A yes-or-no confirmation", () => {
  const openConfirm = async (props: Parameters<typeof Confirmer>[0] = {}) => {
    const user = userEvent.setup();
    render(<DialogProvider><Confirmer {...props} /></DialogProvider>);
    await user.click(screen.getByRole("button", { name: "confirm" }));
    return user;
  };

  it("offers Cancel and Continue, and goes ahead only on Continue", async () => {
    const user = await openConfirm();
    const dialog = screen.getByRole("alertdialog");
    expect(dialog).toHaveAccessibleName("Discard this job?");
    expect(within(dialog).getByText("The draft job is removed.")).toBeInTheDocument();
    expect(within(dialog).getAllByRole("button").map((b) => b.textContent)).toEqual(["Cancel", "Continue"]);
    expect(within(dialog).getByRole("button", { name: "Continue" })).toHaveFocus();

    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await screen.findByText("confirmed: true")).toBeInTheDocument();
  });

  it("does not go ahead on Cancel", async () => {
    const user = await openConfirm();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText("confirmed: false")).toBeInTheDocument();
  });

  it("does not go ahead on Escape", async () => {
    const user = await openConfirm();
    await user.keyboard("{Escape}");
    expect(await screen.findByText("confirmed: false")).toBeInTheDocument();
  });

  it("labels the confirming action, and puts focus on Cancel when confirming is dangerous", async () => {
    const user = await openConfirm({ label: "Discard", danger: true });
    const discard = screen.getByRole("button", { name: "Discard" });
    expect(discard).not.toHaveFocus();
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();

    await user.click(discard);
    expect(await screen.findByText("confirmed: true")).toBeInTheDocument();
  });
});
