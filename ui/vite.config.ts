import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Spec §3.10 — the SPA lives under /ui/ and proxies backend paths to the
// FastAPI server on :8470 during development.
export default defineConfig({
  base: "/ui/",
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8470",
      "/cases": "http://127.0.0.1:8470",
      "/login": "http://127.0.0.1:8470",
      "/logout": "http://127.0.0.1:8470",
      "/benchmark": "http://127.0.0.1:8470",
      "/terms": "http://127.0.0.1:8470",
      "/healthz": "http://127.0.0.1:8470",
      "/readyz": "http://127.0.0.1:8470",
    },
  },
  build: {
    outDir: "dist",
    assetsDir: "assets",
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
