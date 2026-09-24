// Spec §7.1 — the polling contract, tested as pure functions (§12.6).
import { describe, expect, it } from "vitest";
import { intervalFor, pauseDecision, shouldStop, type PollBand } from "./poll-policy";

const CASE_BANDS: PollBand[] = [
  { maxElapsed: 30_000, interval: 5_000 },
  { maxElapsed: 120_000, interval: 10_000 },
];
const BENCH_BANDS: PollBand[] = [{ maxElapsed: 300_000, interval: 5_000 }];

describe("intervalFor (backoff ladder)", () => {
  it("case detail: 5s under 30s", () => {
    expect(intervalFor(0, CASE_BANDS)).toBe(5_000);
    expect(intervalFor(29_999, CASE_BANDS)).toBe(5_000);
  });
  it("case detail: 10s between 30s and 120s", () => {
    expect(intervalFor(30_000, CASE_BANDS)).toBe(10_000);
    expect(intervalFor(119_999, CASE_BANDS)).toBe(10_000);
  });
  it("case detail: 30s beyond the last band", () => {
    expect(intervalFor(120_000, CASE_BANDS, 30_000)).toBe(30_000);
    expect(intervalFor(600_000, CASE_BANDS, 30_000)).toBe(30_000);
  });
  it("benchmark: 5s flat under 300s", () => {
    expect(intervalFor(250_000, BENCH_BANDS)).toBe(5_000);
  });
});

describe("shouldStop (finite stopAfter)", () => {
  it("stops exactly at the deadline", () => {
    expect(shouldStop(599_999, 600_000)).toBe(false);
    expect(shouldStop(600_000, 600_000)).toBe(true);
  });
  it("never stops for null (infinite)", () => {
    expect(shouldStop(10_000_000, null)).toBe(false);
  });
});

describe("pauseDecision (background-tab behavior)", () => {
  it("visible tab never pauses or stops", () => {
    expect(pauseDecision(0, false, 1000)).toEqual({ pause: false, stop: false });
    expect(pauseDecision(10_000, false, 1000)).toEqual({ pause: false, stop: false });
  });
  it("hidden + infinite stopAfter → pause indefinitely, never stop", () => {
    expect(pauseDecision(99_999_999, true, null)).toEqual({
      pause: true,
      stop: false,
    });
  });
  it("hidden + finite not yet reached → pause only", () => {
    expect(pauseDecision(50_000, true, 120_000)).toEqual({
      pause: true,
      stop: false,
    });
  });
  it("hidden + finite exceeded → stop (never resume automatically)", () => {
    expect(pauseDecision(120_000, true, 120_000)).toEqual({
      pause: true,
      stop: true,
    });
  });
});
