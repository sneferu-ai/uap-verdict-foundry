// Spec §7.1 / §12.6 — polling policy as pure functions (unit-testable).
// The hook composes these; the tests exercise them with fake timers.

export interface PollBand {
  /** Switch to the next band when elapsed time exceeds this (ms). */
  maxElapsed: number;
  /** Poll interval while inside this band (ms). */
  interval: number;
}

/** Interval for a given elapsed time. Bands must be ascending. */
export function intervalFor(
  elapsedMs: number,
  bands: PollBand[],
  fallbackInterval = 60_000,
): number {
  for (const band of bands) {
    if (elapsedMs < band.maxElapsed) return band.interval;
  }
  return fallbackInterval;
}

/** Finite stopAfter reached → stop polling, keep last data, show notice. */
export function shouldStop(
  elapsedMs: number,
  stopAfterMs: number | null,
): boolean {
  return stopAfterMs !== null && elapsedMs >= stopAfterMs;
}

/**
 * Background-tab behavior: while document.hidden, pause immediately.
 * - stopAfter finite AND elapsed >= stopAfter → stop permanently.
 * - stopAfter finite but not reached → keep the timer running but do not
 *   fetch while hidden.
 * - stopAfter null (infinite) → pause indefinitely, resume on visible.
 */
export function pauseDecision(
  elapsedMs: number,
  hidden: boolean,
  stopAfterMs: number | null,
): { pause: boolean; stop: boolean } {
  if (!hidden) return { pause: false, stop: false };
  if (stopAfterMs === null) return { pause: true, stop: false };
  return { pause: true, stop: elapsedMs >= stopAfterMs };
}
