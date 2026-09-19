import { createContext, useCallback, useContext, useRef, useState, type ReactNode } from "react";

type Kind = "ok" | "bad" | "warn" | "info";
type Notify = (kind: Kind, content: ReactNode, ms?: number) => void;

const ToastContext = createContext<Notify>(() => {});

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<{ kind: Kind; content: ReactNode } | null>(null);
  const timer = useRef<number | undefined>(undefined);
  const notify = useCallback<Notify>((kind, content, ms = 6000) => {
    window.clearTimeout(timer.current);
    setToast({ kind, content });
    if (ms) timer.current = window.setTimeout(() => setToast(null), ms);
  }, []);
  return (
    <ToastContext.Provider value={notify}>
      {children}
      {toast && (
        <div id="toast" className={`banner ${toast.kind}`} style={{ display: "block" }} role="status" onClick={() => setToast(null)}>
          {toast.content}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);
