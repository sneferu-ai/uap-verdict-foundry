// Intake-form contract pins (round-6 blocker fix).
//
// Two ground-truth rules this page must never violate:
//
// 1. DESIGN.md §11.6 — the backend (intake.py::validate_fields) consumes
//    exactly ONE direction input: `viewing_direction`. There is no
//    `direction` field server-side; a control that submits one is a lying
//    control (SOUL ¶1). So no `direction` control may render — not even
//    when the server supplies `field_options.direction` values.
//
// 2. Backend contract comment (web_json.py:413-415) — `field_options`
//    ships empty lists and "empty option lists tell the SPA to render
//    plain inputs (no invented enums)". Shape is free text today
//    (intake.py:195 pass-through); a select may exist ONLY when the
//    server supplies the options, and then ONLY those options.
//
// The live payload is seeded into the react-query cache exactly as
// web_json.intake_config_payload serves it — no fetch, no invented enums.

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it } from "vitest";
import { NewCasePage } from "./NewCasePage";
import { AuthProvider } from "../auth/AuthContext";
import { BootstrapProvider } from "../bootstrap/BootstrapContext";
import { intakeConfigKey } from "../lib/queries";
import type { IntakeConfig } from "../lib/types";

(globalThis as Record<string, unknown>).IS_REACT_ACT_ENVIRONMENT = true;

/** The real backend payload shape (web_json.py intake_config_payload),
    with the live field_options contract: both lists empty. */
function intakeConfig(overrides?: Partial<IntakeConfig>): IntakeConfig {
  return {
    estimate_text: "Estimated cost: $0.40 (mock).",
    estimated_cost_usd: 0.4,
    cost_mode: "mock",
    expected_lineage_count: 3,
    terms_short:
      "No output from this service claims extraterrestrial origin. " +
      "Story material is labelled speculation and must not be cited " +
      "as evidence. Do not strip non-evidence labels. Verdicts are " +
      "provisional and reflect only the tested mundane-explanation " +
      "categories.",
    media_caps: { image_mb: 50, video_mb: 250, image_mp: 20, video_max_s: 60 },
    field_options: { direction: [], shape: [] },
    estimated_benchmark_cost_usd: null,
    ...overrides,
  };
}

const cleanups: Array<() => Promise<void>> = [];

async function renderIntake(config: IntakeConfig): Promise<HTMLDivElement> {
  const queryClient = new QueryClient({
    // The payload is deliberately injected as the server response for this
    // contract test. Keep it fresh so useQuery does not replace the fixture
    // with an unrelated global fetch mock before assertions run.
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  queryClient.setQueryData(intakeConfigKey, config);
  const container = document.createElement("div");
  document.body.appendChild(container);
  const root: Root = createRoot(container);
  cleanups.push(async () => {
    // Awaited: an un-awaited act(unmount) leaves React's act scope open
    // and swallows the next test's render (found empirically, round 6).
    await act(async () => root.unmount());
    container.remove();
  });
  await act(async () => {
    root.render(
      <MemoryRouter initialEntries={["/ui/cases/new"]}>
        <QueryClientProvider client={queryClient}>
          <BootstrapProvider>
            <AuthProvider>
              <NewCasePage />
            </AuthProvider>
          </BootstrapProvider>
        </QueryClientProvider>
      </MemoryRouter>,
    );
  });
  return container;
}

afterEach(async () => {
  while (cleanups.length > 0) {
    await cleanups.pop()!();
  }
});

describe("NewCasePage intake contract (field_options)", () => {
  it("empty option lists ⇒ plain inputs, no invented enums", async () => {
    const el = await renderIntake(
      intakeConfig({ field_options: { direction: [], shape: [] } }),
    );
    // Shape is free text today — the pre-round-5 behaviour Elias relies on.
    const shapeInput = el.querySelector<HTMLInputElement>("input#shape");
    expect(shapeInput).not.toBeNull();
    expect(shapeInput!.type).toBe("text");
    expect(el.querySelector("select#shape")).toBeNull();
    // Viewing direction (the ONLY direction input the backend consumes)
    // remains present as free text per DESIGN.md §11.6.
    expect(el.querySelector("input#viewing-direction")).not.toBeNull();
  });

  it("never renders a `direction` control — no backend field consumes it", async () => {
    const el = await renderIntake(
      intakeConfig({ field_options: { direction: [], shape: [] } }),
    );
    expect(el.querySelector("#direction")).toBeNull();
    expect(el.querySelector('label[for="direction"]')).toBeNull();
  });

  it("still renders no `direction` control even if the server sends options", async () => {
    // §11.6 is not contingent on the list being empty: a populated
    // `field_options.direction` would still submit into a field the
    // backend silently drops. The control stays absent.
    const el = await renderIntake(
      intakeConfig({
        field_options: { direction: ["north", "south"], shape: [] },
      }),
    );
    expect(el.querySelector("#direction")).toBeNull();
    expect(el.querySelector('label[for="direction"]')).toBeNull();
  });

  it("server-supplied shape options render as a select with exactly those options", async () => {
    const el = await renderIntake(
      intakeConfig({
        field_options: { direction: [], shape: ["disc", "orb", "triangle"] },
      }),
    );
    const select = el.querySelector<HTMLSelectElement>("select#shape");
    expect(select).not.toBeNull();
    expect(el.querySelector("input#shape")).toBeNull();
    const values = Array.from(select!.options).map((o) => o.value);
    // Placeholder + the server's list verbatim, in order. Nothing invented.
    expect(values).toEqual(["", "disc", "orb", "triangle"]);
  });
});
