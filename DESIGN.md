# DESIGN.md — UAP Verdict Foundry Operator Console

## 0. Soul

One sentence: *a dark charcoal foundry console where verdicts are cast, not
celebrated — precise, direct, human* (full brief: [`SOUL.md`](SOUL.md)).

## 1. Reference anchor

- **Reference:** https://linear.app — the Linear app (issue console: inbox,
  my-issues, and project list views), as shipped.
- **Why this one:** SOUL.md ¶2 channels "Linear at 2am". The Foundry and
  Linear solve the same problem: an operator living inside a dense list of
  work items with statuses, for hours, at night, without fatigue. Linear's
  discipline — hairline rules instead of shadows, a muted-tint status pill
  system, exact row heights, a quiet 2px active rail, transform/opacity-only
  motion — is the implementation vocabulary this console needs. The brand
  board (`BRAND_ASSETS/brand-direction.png`) owns shape, color, surface, and
  rhythm; Linear teaches how to *build* that direction in DOM and CSS.

**Five pixel-level patterns we mirror:**

1. **Sidebar nav rows.** Row height 32px; horizontal padding 8px; radius
   6px; label 13px weight-500. The active row gets `bg-elevated` plus a
   2px-wide, 16px-tall rounded rail absolutely positioned at `left: 0` of
   the nav container in `--accent`. Inactive rows: no border, no icon
   color shift — hover only moves background one step (`transparent` →
   `--bg-hover`), 150ms.
2. **List rows (cases table).** Row height 56px on desktop; content separated
   by 1px `--border-subtle` hairlines (no zebra stripes, no row borders);
   case ID 12px tabular mono `--fg-secondary`; status/verdict pills 20px
   tall with a 6px status dot + 12px label; hover elevates the row
   background one step and reveals row actions (which stay focus-visible at
   all times). Numeric columns right-aligned with
   `font-variant-numeric: tabular-nums`.
3. **App header / page chrome.** The main-column page header is 56px tall
   with a 1px bottom hairline (`--border-subtle`) and **no shadow**; page
   title 20px display-face semibold left-aligned, primary action pinned
   right. Nothing floats — chrome is drawn with hairlines on flat planes.
4. **Status pills.** 20px tall, `radius-pill`, 8px horizontal padding,
   muted-tint background at ~14% alpha of the status hue over `--bg-base`,
   text in the status hue lightened to ≥4.5:1 on that tint, 6px dot at the
   left. Hue + dot + label always (never hue alone).
5. **Focus + motion.** Every interactive element: `:focus-visible` ring
   2px `--border-focus` offset 2px. Transitions 150–200ms
   `--ease-default` on `background-color`, `opacity`, `transform` only —
   never layout properties. Route content enters with a single 200ms
   opacity+4px-translate fade staggered once (title → content), then the
   page is still.

**Three deliberate divergences, with citation:**

1. **No ⌘K command palette.** SOUL.md ¶1: eight routes, one operator, six
   keys deep maximum. A palette is ornament at this scale; the sidebar is
   the whole map. (Divergence: judgment — restraint is elegance.)
2. **No indigo.** Linear's identity hue is replaced by the launch-frozen
   brand palette — sage `#ABDFB9` on charcoal `#1C1E25` (`BRAND_IDENTITY.md`
   § Required semantic color tokens are mandatory inputs, not inspiration).
   Status hue *families* from spec §6.3 (amber/emerald/rose/indigo/violet)
   survive, remapped to dark-surface AA values.
3. **No inline row mutations.** Linear edits issues in-place; here every
   cost-bearing action (retry, rerun, benchmark) opens a ConfirmDialog that
   names the maximum cost first, and delete gets its own page (spec §8.8,
   §9.5; B13 operator-accountability law). Nothing spend-bearing happens on
   a hover-revealed affordance.

## Brand direction implementation

Source board: **`BRAND_ASSETS/brand-direction.png`** (direction SHA-256
`3a0cd6a9…ff810d`). The B13 raster remains byte-exact at
`ui/public/brand-mark.png` for provenance verification. The Phase 2 brief's
replacement identity ships as `BRAND_ASSETS/brand-mark.svg`, copied to
`ui/public/brand-mark.svg` and rendered in the sidebar, report,
favicon, and application icon. Personality: precise, direct, human.

- **Shape — "an interlocking path that resolves into one confident
  junction."** Translated twice. (a) The **StageTimeline** on case detail
  renders the eight pipeline stages as one continuous vertical 2px rule in
  `--border-default` (the interlocking path) with stage nodes as 10px
  markers; the terminal verdict badge is the single "confident junction" —
  the only element on the timeline that gets accent treatment. (b) The
  sidebar's active rail (§1 pattern 1) is the same junction motif at nav
  scale: many paths, one lit.
- **Layout — "poster-like hierarchy with one oversized signal and sparse
  supporting detail."** Every screen designates exactly ONE oversized
  signal: case detail renders the status badge + verdict at 13–14px pills
  under a 24px Fraunces case ID; the dashboard's spend is one bar, not a
  KPI band; login is one field on a dark plane. Supporting detail sits in
  12–13px secondary/tertiary type. Section margins step down
  (space-8 → space-4) so rhythm flows downward.
- **Surface — "flat graphic planes with one controlled material
  interruption."** The entire console is flat planes: cards are
  `--bg-elevated` with 1px `--border-subtle` hairlines, `--shadow-0` at
  rest (shadows reserved for the modal layer only). The **one** controlled
  material interruption is the **EvidenceDivider** on case detail and
  report: a full-bleed rule with a centered uppercase label —
  `EVIDENCE FINDINGS — ABOVE / INTERPRETIVE COMMENTARY — BELOW` — drawn in
  `--border-strong` with `--fg-muted` label. It is the seam where the
  product's epistemology becomes visible; nothing else on any surface gets
  this treatment. The fiction page's `bg-rose-700` quarantine banner is a
  *label*, per spec §9.7, not a surface treatment.
- **Typography — "a characterful display face paired with a highly legible
  workhorse sans."** **Fraunces** (variable, opsz 9–144, self-hosted OFL
  woff2) for page titles and the wordmark only — its soft-serif optical
  sizing reads "editorial instrument", not "banking"; **IBM Plex Sans**
  (variable) as the workhorse body/UI face; **IBM Plex Mono** for case IDs,
  hashes, CLI hints, timestamps. All three self-hosted under
  `ui/src/assets/fonts/` so the CSP `font-src 'self'` holds.
- **Motion — "near-instant state changes with one slower orientation
  transition."** State changes (badge swaps, button hovers, pill updates)
  run at `--dur-fast` 150ms. The ONE slower orientation transition is the
  route-change entrance: 300ms `--ease-emphasized` opacity+translate on the
  page container — the moment the operator re-orients. Everything else is
  instant or 150ms; nothing bounces, nothing pulses (spec: no celebratory
  motion on `no_mundane_match`).

## 2. Density

**dense** — an operator console (Linear/Sentry class). Table rows 56px,
13px body in tables, 12px secondary metadata, tight 4pt-based gaps inside
cards, generous 32–48px between page sections.

## 3. Color tokens

The brand palette (`BRAND_IDENTITY.md`) is the mandatory base. Spec §6.2's
light slate/indigo tokens are **superseded** by the launch-frozen brand
direction (orchestrator contract: the board owns color); spec §6.3 status
hue families are preserved and remapped for AA on dark surfaces. Both
themes are designed together in `tokens.css`; dark is the shipped default
(brand), light exists as a supported operator preference. Theme selection
is wired in `ui/src/main.tsx` before first paint: an OS
`prefers-color-scheme: light` preference selects the light theme; an
explicit operator override in `localStorage["uapvf.theme"]` (`"dark"` |
`"light"`) wins over the OS; absent both, dark.

| Role | Dark (default) | Light |
|---|---|---|
| `--bg-base` | `#1C1E25` (brand-background) | `#F6F7F5` |
| `--bg-elevated` | `#24272F` | `#FFFFFF` |
| `--bg-overlay` (modal/backdrop layer) | `#2B2F38` | `#F0F2EF` |
| `--bg-hover` | `#282C35` | `#EDEFEA` |
| `--fg-primary` | `#E9EDE6` | `#20242B` |
| `--fg-secondary` | `#B7C0B4` | `#454B54` |
| `--fg-tertiary` | `#8B9390` | `#6A7280` |
| `--fg-disabled` | `#5C6360` | `#9CA3AF` |
| `--accent` | `#ABDFB9` (brand-primary) | `#2F7A4D` |
| `--accent-hover` | `#C0E9CB` | `#276741` |
| `--accent-pressed` | `#8FCDA0` | `#1F5334` |
| `--accent-ink` (text on accent) | `#14251A` | `#F4FAF5` |
| `--brand-muted` | `#7A9D87` | `#5B7D67` |
| `--brand-secondary` | `#707172` | `#75787B` |
| `--brand-surface` | `#323539` | `#E7E9E4` |
| `--border-subtle` | `#2A2E37` | `#E3E6E0` |
| `--border-default` | `#383D47` | `#D3D7D0` |
| `--border-strong` | `#4A505C` | `#AEB4AC` |
| `--border-focus` | `#ABDFB9` | `#2F7A4D` |
| `--status-running` (queued/analyzing, amber family) | text `#E8C37A` tint `#E8C37A24` | text `#92400E` tint `#FDF3DF` |
| `--status-complete` (emerald family) | text `#86D9A8` tint `#86D9A824` | text `#065F46` tint `#E4F5EC` |
| `--status-verdict` (indigo family, `verdict_ready`/`no_mundane_match`) | text `#A9B4EC` tint `#A9B4EC24` | text `#3730A3` tint `#E8E9FA` |
| `--status-failed` (rose family) | text `#EFA3AE` tint `#EFA3AE24` | text `#9F1239` tint `#FCE8EC` |
| `--status-capped` (orange family) | text `#EDB083` tint `#EDB08324` | text `#9A3412` tint `#FDEBDD` |
| `--status-rerun` (violet family) | text `#C5ADEA` tint `#C5ADEA24` | text `#5B21B6` tint `#EFE8FA` |
| `--status-neutral` (pending/unpaid, slate) | text `#9AA1A8` tint `#9AA1A824` | text `#475569` tint `#E9ECEF` |

Contrast notes (dark theme): `--fg-primary` on `--bg-base` = 12.9:1;
`--fg-secondary` on `--bg-base` = 8.4:1; `--fg-tertiary` on `--bg-base` =
4.8:1; `--accent-ink` on `--accent` = 11.6:1; each status text hue on
`--bg-base` ≥ 4.6:1 (amber 8.6:1, emerald 9.7:1, indigo 7.3:1, rose 7.5:1,
orange 7.9:1, violet 7.2:1, neutral 5.5:1). Status is always hue + dot +
text label, never hue alone.

## 4. Type tokens

- `--font-display`: **Fraunces** (self-hosted variable woff2, opsz-aware;
  weights 500–600 used). Page titles, wordmark, login title. Nothing else.
- `--font-body`: **IBM Plex Sans** (self-hosted variable; 400/500/600).
- `--font-mono`: **IBM Plex Mono** (self-hosted; 400/500). Case IDs,
  hashes, timestamps, CLI hints, `<pre>` fiction/report content.
- Scale (1.25 ratio): `--text-xs` 12px · `--text-sm` 13px · `--text-base`
  14px (mobile body floor 16px applied at <640px via `--text-base-mobile`)
  · `--text-md` 16px · `--text-lg` 18px · `--text-xl` 20px · `--text-2xl`
  24px · `--text-3xl` 30px.
- Line heights: `--line-tight` 1.2 · `--line-snug` 1.35 · `--line-normal`
  1.5 · `--line-relaxed` 1.65.
- Weights: 400 regular, 500 medium, 600 semibold, 700 bold (bold reserved
  for tabular numerals that must anchor a column).

## 5. Spacing tokens

8pt scale with 4pt half-steps for dense interiors: `--space-1` 4px ·
`--space-2` 8px · `--space-3` 12px · `--space-4` 16px · `--space-5` 20px ·
`--space-6` 24px · `--space-8` 32px · `--space-10` 40px · `--space-12`
48px · `--space-16` 64px. Page gutter: 16px mobile / 24px ≥640px / 32px
≥1024px. Section margin-bottom always one step larger than its margin-top.

## 6. Component states

Every interactive component designs all six states:

- **Button (primary):** default `bg: --accent`, `color: --accent-ink`,
  13px/600, 36px tall, radius-md; **hover** `--accent-hover`;
  **focus-visible** 2px `--border-focus` ring offset 2px; **active**
  `--accent-pressed` + `translateY(1px)`; **disabled** opacity 0.45 +
  `cursor-not-allowed` + inline hint text beside the button; **loading**
  disabled + 14px spinner + label switches to present-continuous
  ("Submitting…"). Secondary: transparent bg, 1px `--border-default`,
  `--fg-primary` text, hover `--bg-hover`. Ghost: text only, hover
  `--bg-hover`. Danger: `--status-failed`-derived fill (dark: `#8C2F3F`
  fill `#F6D8DD` text) — the only red surface in the product.
  **Hit zones:** visual heights stay dense (36px default, 28px `btn-sm`);
  every button additionally carries a transparent `::after` pad
  (`hit-4`/`hit-6`/`hit-8` utilities, pads from `--hit-pad-*` tokens) that
  raises the pointer target to ≥ `--hit-target` (44px). Same pattern on
  nav rows and table row-action links.
- **Input:** 36px tall, `bg: --bg-base` (inset on cards), 1px
  `--border-default`, radius-md, 13px text; on <640px viewports inputs and
  selects grow to 44px tall (touch floor — replaced elements cannot carry
  `::after` hit pads); **hover** border
  `--border-strong`; **focus** border `--border-focus` + 2px ring at 35%
  accent alpha; **error** border rose hue + 12px rose message directly
  under the field (`aria-describedby`); **disabled** opacity 0.45 +
  `not-allowed`; **readonly** (cost estimate) `--bg-elevated`, no border,
  "server-provided — not editable" hint in `--fg-tertiary`.
- **Card:** flat `--bg-elevated` + 1px `--border-subtle`, radius-lg,
  padding `--space-6`; **shadow-0 at rest** (border, not shadow); no hover
  state (cards are not clickable; rows inside are). The modal is the only
  elevated surface.
- **Modal (ConfirmDialog):** `--modal-max-w` (480px) panel, `--bg-overlay`
  surface, 1px `--border-default`, radius-lg, `--shadow-3`, backdrop
  `rgba(12,13,17,.6)` + 4px blur; title 16px semibold, body 13px
  `--fg-secondary`; rendered in a portal at the end of `<body>` (route
  content animates with a transform, which would otherwise become the
  containing block for the fixed backdrop); `aria-labelledby` +
  `aria-describedby` (the description carries the id); initial
  focus on the confirm button; Esc + backdrop click close; focus returns to
  trigger.
- **Nav row:** §1 pattern 1 — default transparent; hover `--bg-hover`;
  focus-visible 2px ring inset; active `--bg-elevated` + 2px accent rail;
  disabled n/a; loading n/a.
- **Table:** header row 32px, 11px uppercase letterspacing 0.06em
  `--fg-tertiary`, no background (hairline below); body rows §1 pattern 2;
  empty tbody renders an `EmptyState` row spanning all columns.
- **Badge/pill:** §1 pattern 4; states: default; **hover** none (badges are
  not interactive — if a badge needs to be clickable it is a link, styled
  as one); focus ring when rendered as a link.
- **Toast (sonner):** bottom-right, `--bg-overlay` surface, 1px
  `--border-default`, radius-md, `--shadow-2`, 13px text, auto-dismiss 4s,
  `aria-live="polite"` region.
- **Dropzone (intake media, spec §9.3):** wraps the native file input —
  default 1px dashed `--border-default`; **drag-over** dashed border steps
  to `--accent` + `bg-inset` (150ms background-color only); a drop runs
  the identical pre-upload validation pipeline as the chooser before any
  network transfer. Caption states "One media file per case."

## 7. Motion

- Durations: `--dur-instant` 50ms · `--dur-fast` 150ms · `--dur-base`
  200ms · `--dur-slow` 300ms. Easings: `--ease-default`
  cubic-bezier(0.4,0,0.2,1) · `--ease-emphasized` cubic-bezier(0.2,0,0,1).
- State changes 150ms; route entrance 300ms emphasized (the ONE slower
  orientation transition — brand direction). Animate `transform` +
  `opacity` only.
- **Skeletons not spinners** on every initial load: dashboard = 5 table
  rows of shimmering bars; case detail = header pill + timeline ghosts;
  report/fiction = section ghosts; intake = form field ghosts. Skeleton
  bars: `--bg-hover` fill, 1.2s opacity shimmer 40%→70%, radius-sm.
  Spinners exist only inside buttons (async mutations) and as refetch
  indicators.
- `prefers-reduced-motion: reduce`: shimmer and entrance disabled
  (opacity-only, 0ms translate).

## 8. Voice & copy

One voice — the product's: precise, direct, human; never cute, never
dramatic. Buttons are imperative verbs; states are facts; errors name what
failed and the next step.

- Empty states: "No cases yet. Submit the first capture to start a
  pipeline." · "No benchmark has been run. Preregister thresholds from the
  CLI, then run it here." · "No audit events recorded for this case yet."
- Errors: "Cannot reach server. Check that `uapvf serve` is running." ·
  "Session expired. Sign in again to continue." · "File exceeds size cap of
  50 MB."
- Button labels: "Sign in" · "Submit case" · "Delete permanently".

## 9. Anti-defaults forbidden in this project

1. **X-Files kitsch** — no glow, radar sweeps, alien iconography, or "UFO"
   emoji; no celebratory treatment of `no_mundane_match` (spec §6.1.1).
2. **Dashboard-stat hero** — no KPI card band; spend is one bar (SOUL ¶3.2).
3. **Generic admin template** — no Inter/Roboto, no slate-50 + indigo-600,
   no soft-drop-shadow-on-every-card (SOUL ¶3.3; shadows live on modals).
4. **Tabloid voice** — no "Oops!", no exclamations, no consumer-cute copy
   beside product-terse copy (SOUL ¶3.4).
5. No white-to-purple gradients; no pure `#000`/`#FFF`; no seven-color
   status rainbow (seven families exist but a screen shows at most the ones
   its rows carry); no emoji as functional icons (lucide-react only); no
   `dangerouslySetInnerHTML` (spec §4.1 — ESLint-enforced); no animating
   layout properties; no placeholder-only labels; no >12 visible controls
   per panel (intake uses progressive disclosure).

## 10. Audit

- Token audit: `grep -RnE '#[0-9a-fA-F]{3,8}|[0-9]+px|[0-9]+ms|rgba?\(|hsla?\(' ui/src` must
  hit only `tokens.css`, `index.css` (`@theme` wiring), and font/`@keyframes`
  internals (see `scripts/audit-tokens.sh` — runs the grep and fails on
  leaks outside the allow-list).
- Backend contract: `python3 -m pytest tests` (256 tests). The SPA's data
  plane — Accept negotiation matrix, `Vary: Accept` coverage, typed error
  codes, login JSON contract, case-list/detail/intake-config/report/fiction
  JSON shapes, and the SPA shell + `uapv-bootstrap` injection + infra
  routes — is covered by `tests/test_web_json_ui.py` (34 tests, incl. the
  withheld-fiction derivation matrix); the remaining 222 are the product's
  backend suite.
- Frontend units: `cd ui && npm run test` (vitest: format helpers, and the
  polling policy — bands / background-pause / finite-stop — extracted as
  pure functions in `ui/src/lib/poll-policy.ts` per spec §12.6).
- Manual six-category + state walk recorded in the round summary.

## 11. Grounded-truth deviations (verified against the live backend)

Where UI_SPEC's idealized contract and the real backend disagreed, the
backend won (it is the ground truth) and the SPA follows it:

1. **Report sidecar shape** — the spec §7.6 fields `evidence_section`,
   `interpretation_section`, `disclaimer`, and `report_sha256` do not
   exist in the real `report.json`. The report page renders the real
   fields: `battery_results[]` with structured `source_stamp` objects,
   `lineage_outputs[]`, `warnings[]`, `verdict_text`, a structured
   `uncertainty` object (`value` / `calibrated` / `uncertainty_reason`),
   and `provenance.media_sha256` as the integrity reference. The
   evidence/interpretation divider sits between the battery+lineage
   tables (evidence) and the verdict text (interpretation).
2. **Pipeline stage names** — the real stage set is
   `intake, quality_gate, lineage_analysis, rigor_adjudication, battery,
   coverage_check, verdict, uncertainty, report, fiction`
   (ten stages, FR-004 order); the case-detail
   timeline renders exactly these in pipeline order.
3. **Fiction verdict gating** — a `mundane_identified` case has no
   fiction seed by construction; the fiction page says so explicitly
   instead of showing an error.
4. **Bootstrap `estimated_benchmark_cost_usd`** may be `null` until a
   benchmark has run; the benchmark dialog then shows "Cost estimate
   unavailable. The run will still cost money." — never a fabricated
   number.
5. **Brand routes** — `dist/brand-mark.svg` and `dist/app-icon.svg` sit at
   the dist root, not under `dist/assets/`, so the server registers explicit
   routes (the SPA catch-all would otherwise serve `index.html`). The old
   byte-exact PNG remains available only for B13 provenance compatibility.
6. **Spec §9.3 `direction` select has no backend field** — the real
   backend (`intake.py::validate_fields`) accepts exactly one direction
   input: `viewing_direction`, parsed numerically as `"az"` or `"az,el"`
   degrees (azimuth ∈ [0,360), elevation ∈ [-90,90], elevation default
   45). `field_options.direction` ships empty and nothing server-side
   consumes a `direction` form field — an added select would submit data
   the backend silently drops (a lying control, SOUL ¶1). The intake form
   therefore ships `viewing_direction` as the direction control, with the
   backend's numeric contract as its hint and a client-side mirror of
   `parse_viewing_direction` validating before submit. **Round 6 (blocker
   closure):** a round-5 pass had re-introduced a dead `direction` select
   and converted shape to a select, trusting spec §9.3's idealized text
   over this section; both were removed/restored. The binding contract
   for `field_options` is the backend's own comment (web_json.py:413-415):
   *empty option lists tell the SPA to render plain inputs (no invented
   enums)*. So: no `direction` control exists at all (even against a
   populated `field_options.direction` — the absence is unconditional,
   because the backend field does not exist), and shape renders a
   `<select>` ONLY when `field_options.shape` is non-empty — otherwise a
   plain free-text input ("disc", "orb"), which intake.py:195 accepts
   verbatim. Pinned by `ui/src/pages/NewCasePage.test.tsx` (jsdom render
   against the live `{direction: [], shape: []}` payload: plain inputs,
   no direction control; populated shape list ⇒ select with exactly the
   server's options; populated direction list ⇒ still no control).
   **Procedural rule (two consecutive honesty-class rounds):** any
   control added from spec text must be checked against this §11 and the
   live payload before it ships.
7. **Fiction withholding is a derived state, not a column** — the cases
   schema carries no `fiction_withheld` column. The fiction stage's
   designed terminal (stage COMPLETES, output cleared after label
   validation failure: `fiction_ready=1`, `fiction_path=NULL`,
   `status='complete'`, verdict unresolved) is derived by
   `web_json._fiction_withheld` and served identically from both
   `case_detail_payload` sites and `fiction_payload`. The SPA renders the
   withheld card on the fiction page (no CLI hint there — the CLI has no
   content to print), keeps the deliverables button enabled, and states
   "Fiction seed withheld after label validation failure." beside it. The
   `status='complete'` guard keeps a mid-rerun window reading "not ready"
   truthfully; `mundane_identified` lands the same cleared row shape but
   never attempted a seed, so it is excluded from withholding (§11.3).
   Pinned by `tests/test_web_json_ui.py::TestFictionWithheldDerivation`.
