// Spec §12.6 — unit coverage for the deterministic lib layer.
import { describe, expect, it } from "vitest";
import {
  formatAgreementPercent,
  formatCalibrated,
  formatPercent1,
  formatScore,
  formatUncertainty,
  formatUsd,
  formatUtc,
  shortId,
} from "./format";

describe("formatUncertainty", () => {
  it("two decimals for numbers", () => {
    expect(formatUncertainty(0.62)).toBe("0.62");
    expect(formatUncertainty(0.6)).toBe("0.60");
  });
  it("Not computed for null", () => {
    expect(formatUncertainty(null)).toBe("Not computed");
  });
});

describe("formatCalibrated", () => {
  it("maps the 0/1 flag", () => {
    expect(formatCalibrated(1)).toBe("Yes");
    expect(formatCalibrated(0)).toBe("No");
    expect(formatCalibrated(null)).toBe("Not computed");
  });
});

describe("formatAgreementPercent", () => {
  it("rounds to whole percent", () => {
    expect(formatAgreementPercent(0.667)).toBe("67%");
    expect(formatAgreementPercent(null)).toBeNull();
  });
});

describe("formatScore / formatPercent1 / formatUsd", () => {
  it("score two decimals or dash", () => {
    expect(formatScore(0.732)).toBe("0.73");
    expect(formatScore(null)).toBe("—");
  });
  it("percent one decimal", () => {
    expect(formatPercent1(0.7)).toBe("70.0%");
    expect(formatPercent1(null)).toBe("—");
  });
  it("usd two decimals", () => {
    expect(formatUsd(0.02)).toBe("$0.02");
    expect(formatUsd(null)).toBe("—");
  });
});

describe("formatUtc (§6.7 — always UTC, never local)", () => {
  it("renders YYYY-MM-DD HH:MM UTC", () => {
    expect(formatUtc("2026-08-20T06:40:12.123Z")).toBe("2026-08-20 06:40 UTC");
    expect(formatUtc("2026-08-20T06:40:00+00:00")).toBe("2026-08-20 06:40 UTC");
  });
  it("dash for null", () => {
    expect(formatUtc(null)).toBe("—");
  });
});

describe("shortId", () => {
  it("first eight chars", () => {
    expect(shortId("20260820T0640-abcd-1234")).toBe("20260820");
  });
});
