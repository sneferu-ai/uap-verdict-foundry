// System status popover (spec §5.2 + §11 popover contract): healthz +
// readyz polled every 30s, degraded state shown honestly. Click opens
// (UI-35 — hover-only breaks Safari mouse users, where clicking a button
// does not focus it); Escape closes and returns focus to the trigger;
// outside click closes.

import { useEffect, useId, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, CircleEllipsis, XCircle } from "lucide-react";
import { jsonGet } from "../lib/api";
import type { HealthResponse, ReadinessResponse } from "../lib/types";

function useSystemStatus() {
  const health = useQuery<HealthResponse>({
    queryKey: ["healthz"],
    queryFn: () => jsonGet<HealthResponse>("/healthz"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    retry: 1,
  });
  const ready = useQuery<ReadinessResponse>({
    queryKey: ["readyz"],
    queryFn: () => jsonGet<ReadinessResponse>("/readyz"),
    refetchInterval: 30_000,
    refetchIntervalInBackground: false,
    retry: 1,
  });
  return { health, ready };
}

export function SystemStatusPopover() {
  const { health, ready } = useSystemStatus();
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const titleId = useId();
  const panelId = useId();

  const degraded =
    health.isError || (health.data ? health.data.status !== "ok" : false);
  const notReady =
    ready.isError || (ready.data ? ready.data.ready !== true : false);

  // Outside click closes; Escape closes and returns focus to the trigger
  // (spec §11 popover contract).
  useEffect(() => {
    if (!open) return;
    const onDocPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    };
    document.addEventListener("pointerdown", onDocPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onDocPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  const statusLabel =
    health.isLoading || ready.isLoading
      ? "checking"
      : degraded || notReady
        ? "degraded"
        : "operational";

  return (
    <div ref={rootRef} className="relative">
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-label={`System status: ${statusLabel}${open ? "" : ". Opens details"}`}
        aria-expanded={open}
        aria-controls={panelId}
        aria-haspopup="dialog"
        className="relative flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-xs text-ink-2 hover:bg-hovered hit-6"
      >
        {degraded || notReady ? (
          <XCircle aria-hidden="true" className="h-3.5 w-3.5 text-st-run" />
        ) : health.isLoading || ready.isLoading ? (
          <CircleEllipsis aria-hidden="true" className="h-3.5 w-3.5 text-ink-disabled" />
        ) : (
          <CheckCircle2 aria-hidden="true" className="h-3.5 w-3.5 text-st-ok" />
        )}
        <span
          className={
            degraded || notReady
              ? "text-st-run"
              : health.isLoading || ready.isLoading
                ? "text-ink-disabled"
                : "text-ink-2"
          }
        >
          {health.isLoading || ready.isLoading
            ? "System status…"
            : degraded || notReady
              ? "Degraded"
              : "System healthy"}
        </span>
      </button>
      <div
        id={panelId}
        role="dialog"
        aria-modal="false"
        aria-labelledby={titleId}
        className={[
          "absolute bottom-full left-0 mb-2 w-64 rounded-md border border-line-2 bg-overlay p-3 text-xs shadow-2",
          "transition-[opacity] duration-[var(--dur-fast)]",
          open ? "visible opacity-100" : "invisible opacity-0",
        ].join(" ")}
      >
        <div className="flex items-center justify-between">
          <span id={titleId} className="font-medium text-ink">System status</span>
          <span className="font-mono text-ink-3 tnum">poll 30s</span>
        </div>
        <div className="mt-2 flex flex-col gap-1.5">
          <div className="flex items-center justify-between">
            <span className="text-ink-2">Analysis mode</span>
            <span className="font-mono uppercase text-ink-2">
              {ready.data?.mode ?? "unknown"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-2">API</span>
            <span
              className={
                health.isError
                  ? "text-st-bad"
                  : degraded
                    ? "text-st-run"
                    : health.isLoading
                      ? "text-ink-disabled"
                      : "text-st-ok"
              }
            >
              {health.isError
                ? "Unreachable"
                : health.isLoading
                  ? "Checking…"
                  : health.data?.status ?? "Unknown"}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-ink-2">Pipeline</span>
            <span
              className={
                ready.isError
                  ? "text-st-bad"
                  : notReady
                    ? "text-st-run"
                    : ready.isLoading
                      ? "text-ink-disabled"
                      : "text-st-ok"
              }
            >
              {ready.isError
                ? "Unreachable"
                : ready.isLoading
                  ? "Checking…"
                  : ready.data
                    ? ready.data.ready
                      ? "Ready"
                      : "Not ready"
                    : "Unknown"}
            </span>
          </div>
          {(degraded || notReady) && (
            <p className="mt-1 text-st-run">
              One or more subsystems report a degraded state. Actions are
              not production-cleared; inspect <span className="font-mono">/readyz</span>
              before accepting a live case.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
