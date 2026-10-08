import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { initLocale } from "./i18n";
import "./styles.css";
import "./pages.css";

// The saved interface language loads before the first paint, so the page never flashes English.
void initLocale().then(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <App />
    </StrictMode>,
  ),
);
