import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { DialogProvider } from "./components/Dialog";
import { ToastProvider } from "./components/Toast";
import { JobLayout } from "./components/JobContext";
import { ConfigPage } from "./pages/ConfigPage";
import { GlossaryPage } from "./pages/GlossaryPage";
import { JobsPage } from "./pages/JobsPage";
import { ProgressPage } from "./pages/ProgressPage";
import { ReviewPage } from "./pages/ReviewPage";
import { SeriesListPage, SeriesPage } from "./pages/SeriesPage";
import "./styles.css";
import "./pages.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <ToastProvider>
      <DialogProvider>
        <BrowserRouter>
          <Routes>
            <Route path="/" element={<JobsPage />} />
            <Route path="/series" element={<SeriesListPage />} />
            <Route path="/series/:seriesId" element={<SeriesPage />} />
            <Route path="/jobs/:jobId" element={<JobLayout />}>
              <Route index element={<Navigate to="config" replace />} />
              <Route path="config" element={<ConfigPage />} />
              <Route path="glossary" element={<GlossaryPage />} />
              <Route path="progress" element={<ProgressPage />} />
              <Route path="review" element={<ReviewPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </BrowserRouter>
      </DialogProvider>
    </ToastProvider>
  </StrictMode>,
);
