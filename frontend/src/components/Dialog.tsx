import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export type DialogAction<T extends string> = { value: T; label: string; primary?: boolean; danger?: boolean };
type Request = {
  title: string;
  body: ReactNode;
  actions: DialogAction<string>[];
  resolve: (value: string | null) => void;
};
type Ask = <T extends string>(title: string, body: ReactNode, actions: DialogAction<T>[]) => Promise<T | null>;

const DialogContext = createContext<Ask>(async () => null);

/** In-app replacement for window.confirm: one modal at a time, resolved with the chosen action. */
export function DialogProvider({ children }: { children: ReactNode }) {
  const [request, setRequest] = useState<Request | null>(null);
  const ask = useCallback<Ask>(
    (title, body, actions) =>
      new Promise((resolve) => {
        setRequest((old) => {
          old?.resolve(null);
          return { title, body, actions, resolve: resolve as (value: string | null) => void };
        });
      }),
    [],
  );
  const close = (value: string | null) => {
    request?.resolve(value);
    setRequest(null);
  };
  return (
    <DialogContext.Provider value={ask}>
      {children}
      {request && <DialogView request={request} onClose={close} />}
    </DialogContext.Provider>
  );
}

function DialogView({ request, onClose }: { request: Request; onClose: (value: string | null) => void }) {
  const primary = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    primary.current?.focus();
    // Capture phase so Escape cancels this dialog before any page handler sees it.
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      onClose(null);
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose(null); }}>
      <section className="card modal dialog" role="alertdialog" aria-modal="true" aria-labelledby="dialog-title">
        <h2 id="dialog-title">{request.title}</h2>
        <div className="dialog-body">{request.body}</div>
        <div className="row dialog-actions">
          {request.actions.map((action) => (
            <button
              key={action.value}
              ref={action.primary ? primary : undefined}
              className={action.primary ? "primary" : action.danger ? "danger" : ""}
              onClick={() => onClose(action.value)}
            >
              {action.label}
            </button>
          ))}
        </div>
      </section>
    </div>
  );
}

/** Ask a question with custom actions; resolves to the chosen value, or null when dismissed. */
export const useDialog = () => useContext(DialogContext);

/** Yes/no confirmation; resolves true only for the confirming action. */
export function useConfirm() {
  const ask = useDialog();
  return useCallback(
    async (title: string, body: ReactNode, confirmLabel = "Continue", danger = false) =>
      (await ask(title, body, [
        { value: "cancel", label: "Cancel" },
        { value: "ok", label: confirmLabel, primary: !danger, danger },
      ])) === "ok",
    [ask],
  );
}
