// UI spec §8.1 — polling with elapsed-time bands. Elapsed time counts
// VISIBLE time only (D-38): a tab hidden for 10 minutes consumes no
// polling budget. Interaction resumes a stopped cycle at the first band.

import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";

export interface PollingBand {
  /** Upper edge of the band in ms of visible elapsed time. */
  maxElapsed: number;
  /** Poll interval in ms while elapsed is below maxElapsed. */
  interval: number;
}

export interface UsePollingOptions<T> {
  queryKey: unknown[];
  queryFn: () => Promise<T>;
  bands: PollingBand[];
  stopAfter: number;
  resumeOnInteraction?: boolean;
  pauseWhenHidden?: boolean;
  enabled?: boolean;
  staleTime?: number;
}

export interface UsePollingResult<T> {
  data: T | undefined;
  error: Error | null;
  isStopped: boolean;
  resume: () => void;
  isLoading: boolean;
}

const TICK_MS = 1000;

/** Case detail schedule (spec §8.1): 5s to 60s, 10s to 180s, 30s to 300s. */
export const CASE_DETAIL_BANDS: PollingBand[] = [
  { maxElapsed: 60_000, interval: 5_000 },
  { maxElapsed: 180_000, interval: 10_000 },
  { maxElapsed: 300_000, interval: 30_000 },
];
export const CASE_DETAIL_STOP_MS = 300_000;

/** Benchmark schedule (spec §8.1): 5s to 60s, 10s to 300s, 30s to 1200s. */
export const BENCHMARK_BANDS: PollingBand[] = [
  { maxElapsed: 60_000, interval: 5_000 },
  { maxElapsed: 300_000, interval: 10_000 },
  { maxElapsed: 1_200_000, interval: 30_000 },
];
export const BENCHMARK_STOP_MS = 1_200_000;

export function usePolling<T>(options: UsePollingOptions<T>): UsePollingResult<T> {
  const {
    queryKey,
    queryFn,
    bands,
    stopAfter,
    resumeOnInteraction = true,
    pauseWhenHidden = true,
    enabled = true,
    staleTime = 0,
  } = options;

  const elapsedRef = useRef(0);
  const [isStopped, setIsStopped] = useState(false);
  const stoppedRef = useRef(false);
  stoppedRef.current = isStopped;

  const resetRef = useRef(() => {
    elapsedRef.current = 0;
    setIsStopped(false);
  });

  // Advance the visible-time clock. Hidden tabs consume nothing (D-38).
  useEffect(() => {
    if (!enabled) return;
    const timer = window.setInterval(() => {
      if (pauseWhenHidden && document.visibilityState === "hidden") return;
      if (stoppedRef.current) return;
      elapsedRef.current += TICK_MS;
      if (elapsedRef.current >= stopAfter) {
        setIsStopped(true);
      }
    }, TICK_MS);
    return () => window.clearInterval(timer);
  }, [enabled, stopAfter, pauseWhenHidden]);

  // Resume on user interaction once stopped (spec §8.1 resumed state).
  useEffect(() => {
    if (!enabled || !resumeOnInteraction) return;
    const resume = () => {
      if (stoppedRef.current) resetRef.current();
    };
    window.addEventListener("pointerdown", resume);
    window.addEventListener("keydown", resume);
    return () => {
      window.removeEventListener("pointerdown", resume);
      window.removeEventListener("keydown", resume);
    };
  }, [enabled, resumeOnInteraction]);

  const query = useQuery<T>({
    queryKey,
    queryFn,
    enabled,
    staleTime,
    refetchInterval: () => {
      if (!enabled) return false;
      if (pauseWhenHidden && document.visibilityState === "hidden") {
        return false;
      }
      if (stoppedRef.current) return false;
      const elapsed = elapsedRef.current;
      if (elapsed >= stopAfter) return false;
      for (const band of bands) {
        if (elapsed < band.maxElapsed) return band.interval;
      }
      return false;
    },
  });

  return {
    data: query.data,
    error: query.error instanceof Error ? query.error : null,
    isStopped,
    resume: () => resetRef.current(),
    isLoading: query.isLoading,
  };
}
