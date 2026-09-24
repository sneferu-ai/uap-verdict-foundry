import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router";
import { QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { BootstrapProvider } from "./bootstrap/BootstrapContext";
import { AuthProvider, ToastViewport } from "./auth/AuthContext";
import { ErrorBoundary } from "./components/error-boundary";
import { queryClient } from "./lib/query-client";
import "./index.css";

// Theme wiring (DESIGN.md §3): dark is the shipped default; an OS light
// preference selects the designed light theme, and an explicit operator
// override in localStorage["uapvf.theme"] ("dark" | "light") wins. Runs
// before React mounts so first paint is already correct.
function applyTheme(): void {
  const stored = window.localStorage.getItem("uapvf.theme");
  const theme =
    stored === "light" || stored === "dark"
      ? stored
      : window.matchMedia("(prefers-color-scheme: light)").matches
        ? "light"
        : "dark";
  document.documentElement.dataset.theme = theme;
}

applyTheme();
window
  .matchMedia("(prefers-color-scheme: light)")
  .addEventListener("change", () => {
    // Only follow the OS while the operator has not pinned a choice.
    if (window.localStorage.getItem("uapvf.theme") === null) applyTheme();
  });

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("missing #root element");

createRoot(rootEl).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <BootstrapProvider>
        <BrowserRouter>
          <AuthProvider>
            <ErrorBoundary>
              <App />
            </ErrorBoundary>
            <ToastViewport />
          </AuthProvider>
        </BrowserRouter>
      </BootstrapProvider>
    </QueryClientProvider>
  </StrictMode>,
);
