import type { ReactNode } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DialogProvider } from "./components/Dialog";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { JobLayout } from "./components/JobContext";
import { ToastProvider } from "./components/Toast";
import { ConfigPage } from "./pages/ConfigPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { JobsPage } from "./pages/JobsPage";
import { ProgressPage } from "./pages/ProgressPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SeriesListPage, SeriesPage } from "./pages/SeriesPage";
import { TextPage } from "./pages/TextPage";

/** Everything a page needs around it. Toasts and dialogs sit inside the router so their content can use <Link>. */
export function AppProviders({ children }: { children: ReactNode }) {
  return (
    <ErrorBoundary>
      <BrowserRouter>
        <ToastProvider>
          <DialogProvider>{children}</DialogProvider>
        </ToastProvider>
      </BrowserRouter>
    </ErrorBoundary>
  );
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/" element={<JobsPage />} />
      <Route path="/series" element={<SeriesListPage />} />
      <Route path="/series/:seriesId" element={<SeriesPage />} />
      <Route path="/jobs/:jobId" element={<JobLayout />}>
        <Route index element={<Navigate to="config" replace />} />
        <Route path="config" element={<ConfigPage />} />
        <Route path="glossary" element={<GlossaryPage />} />
        <Route path="progress" element={<ProgressPage />} />
        <Route path="text" element={<TextPage />} />
        <Route path="review" element={<ReviewPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export function App() {
  return (
    <AppProviders>
      <AppRoutes />
    </AppProviders>
  );
}
