import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// The build lands inside the Python package so `pip install` ships the UI
// without Node. `npm run dev` proxies the API to a running `book-agent ui`.
export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../book_agent/web/static",
    emptyOutDir: true,
    chunkSizeWarningLimit: 800,
  },
  server: {
    proxy: { "/api": { target: "http://127.0.0.1:8765", changeOrigin: true } },
  },
  test: { environment: "node" },
});
