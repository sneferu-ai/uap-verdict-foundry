# Implementation Notes

## Production finalizer (2026-08-21)

This pass reconciled the completed implementation with the original retained
Phase 2 contract and then exercised the product as an installed application.

### Live-provider and certification completion

- The aircraft adapter now speaks the official ADS-B Exchange radius API with
  `x-api-key`, or OpenSky state vectors with optional OAuth client credentials.
  It checks the provider timestamp against `observed_at`; a live-only response
  cannot silently answer a historical question. Anonymous OpenSky is explicitly
  evaluation-only in readiness, and an empty custom-gateway response is negative
  only when it asserts `coverage_confirmed: true`.
- The scheduled orbital source accepts current OMM JSON as well as legacy TLE,
  including six-digit catalog IDs. It validates the payload, records exact and
  substantive hashes, scopes lookup to the configured source URL, and respects
  the provider refresh interval across restarts. Satellite matching now performs
  real Skyfield/SGP4 propagation across the configured time window and compares
  the propagated topocentric direction to the supplied viewing direction.
- L6 is no longer a placeholder. It runs the pinned ONNX Model Zoo int8
  ResNet-50 in the network-denied OS sandbox with documented ImageNet
  preprocessing and a deliberately narrow aircraft/bird/insect mapping. The
  installer checks the pinned supplier SHA-256 and produces an Ed25519 operator
  receipt; every inference and readiness probe re-verifies the signature,
  weights, mapping, public-key pin, and mapping contract.
- `uapvf lineages record-eval` now builds the two production evaluation
  manifests only from actual seven-lineage sandbox outputs over operator media.
  Labels remain blinded during inference, every media item is hashed, collection
  composition is validated before promotion, interrupted runs resume from
  checkpoints, and a single-recorder lock prevents concurrent promotion.
  Independence and calibration reports are bound to the exact manifest hash and
  recorder provenance; changing the manifest invalidates both live gates.
- Live readiness now blocks on all four material deployment conditions: real
  operator certification, verified signed model artifacts, credentialed
  production ADS-B, and a fresh configured-source orbital catalog. The complete
  operator procedure and collection schema are documented in
  `docs/LIVE_CERTIFICATION.md`.

### Defects found and corrected

- The generated React console was not part of the Python wheel. The production
  build is now frozen into `uapvf/ui_dist`, included as package data, and
  exercised from a clean wheel install.
- The first Claudopus bridge targeted an invented xenoscience route. Structured
  reviews now use the public, stateless `POST /sdk/judge` contract. A final live
  contract test then found that product-local reviewer role names were not in
  the active Claudopus cast; the adapter now omits `roles` so Claudopus selects
  its documented distinct scorer panel. Readiness probes that exact contract
  with a stateless no-dispatch request instead of assuming `/healthz` exists.
- Unknown/unavailable lineage outputs could resemble agreement. Concordance now
  requires comparable classifications and keeps coverage, agreement,
  confidence, variance, and certainty separate.
- Feature-only lineages could be interpreted as negative drone/bird/balloon/
  meteor results even though they did not cover those taxonomies. Those checks
  now stay `insufficient` unless explicit taxonomy coverage or a meaningful
  closed-taxonomy classification exists. The artifact battery also excludes
  unavailable lineages from its examined count.
- A completed deletion audit event could be written before backup scrubbing and
  restore proof. Deletion now records a request first and records completion
  only after the sensitive-material lifecycle is actually complete; legal hold
  still blocks the operation before mutation.
- Legacy reruns could omit `media_kind`. Intake now persists it for new cases
  and repairs it when a legacy case is rerun.
- Positive verdict citations could end in doubled punctuation. Verdict text is
  normalized and regression-tested.
- The legacy `/brand-mark.png` route and login surface could use the provisional
  raster mark. The route remains compatible, while all primary surfaces use the
  new SVG identity and packaged icon system.
- Early story plates were generic and one comic caption overlapped another.
  The final governed renderer produces three distinct 1600×900 editorial
  scenes plus a six-second H.264 clip; layout was pixel-reviewed, and visible
  and machine-readable non-evidence labels, pHash screening, metadata, alt text,
  and multi-timestamp video checks remain mandatory.
- Video support was not proven end to end. A real six-second MP4 with audio was
  uploaded, normalized, sampled across timestamps, analyzed by trajectory and
  audio lineages, correctly identified as a drone, reported, and delivered with
  byte-range streaming.
- Buyer delivery previously lacked a production customer boundary. Delivery
  now uses hashed expiring/revocable capabilities, signed package assets,
  Ed25519 receipts, cross-case isolation, redemption/rate limits, stale-package
  rejection, and no operator token or filesystem disclosure.
- Retention, TLE refresh, canon direction, the 11-category battery, OS-level
  hostile-media isolation, seven-lineage adjudication, report evidence/story
  separation, source-stamp presentation, observability, deployment, recovery,
  and the named feature-overlap comparison were completed and tested rather
  than left as backlog entries.

### Final evidence

- Backend: **393 passed** (complete suite), including real-provider schemas,
  signed-model tampering, OMM/six-digit catalog support, certification resume,
  exact-manifest binding, and provenance rejection.
- Frontend: **24 passed**; lint and design-token audit pass; production build
  passes; `npm audit` reports zero vulnerabilities.
- Package: wheel and sdist build; clean wheel import contains the branded UI and
  defaults; wheel inspection finds no case, backup, temporary, database, or
  SQLite runtime data.
- Live demonstration: image and real-video cases both complete; image case
  contains four governed story assets; buyer page returns 200; signed story
  video returns 206 for a byte-range request; readiness is true; Claudopus
  contract is reachable; all six frozen Phase 2 references validate in strict
  mode; the 196-entry audit chain verifies with no fork or problem.

### Honest remaining deployment prerequisites

The included investor demonstration runs in visibly labelled `simulation`
mode. It now uses a real verified signed ResNet model and a real current
CelesTrak OMM catalog, and the production ADS-B adapters are implemented and
tested. Promotion to `live` still intentionally fails closed until the operator
provides a production ADS-B credential and a genuine labelled media collection
whose actual sandbox outputs pass all 21 independence pairs and all seven
calibration thresholds. Those two inputs cannot be fabricated by a finalizer.
Claudopus `/sdk/judge` itself is reachable and returned a non-mock judgment from
two distinct observed model lineages. Interactive games and a broad multi-user
SaaS/payment platform remain outside the controlling specification.

## Round 5 (2026-08-21)

Reviewer round-4 verdict: ACCEPTED, no blockers, no questions. Round-5
scope is the reviewer's FOCUS_NEXT: `pipeline.py` + `verdict.py` — the
live-path wiring of `evaluate_operator_gates` into `compute_concordance`
at the pipeline call site. l1–l7 implementations and the sandbox remain
staged per the reviewer's own sequencing.

### 1. Concordance at the pipeline call site (`pipeline.py`)

- `compute_lineage_rigor(lineages, settings)` is the pipeline's
  `compute_concordance` call site (spec §3.3). When the stage's lineage
  outputs carry the §3.2 seven-lineage contract (parse through
  `LineageOutput.from_dict`, ids within `declared_lineage_ids()`, no
  duplicates), it scores them with the classification-gated
  refutation-consistent metric:
  - **live** (default): condition 4/5 booleans come from
    `rigor.evaluate_operator_gates(settings.var_dir)` — derived
    fail-closed from the stored operator certification artifacts, never
    trusted (round-3/4 validator behavior, now on the live path). The
    gate reasons are recorded in the stage output (`operator_gates`)
    and the `rigor_evaluated` audit event.
  - **simulation**: gates are live-mode inputs only
    (`runtime_simulation` reasons recorded); rigor requires [1, 2, 6]
    per `rigor_config.json`.
  - **mock**: blocked outright (`runtime_blocked`).
  - Pre-contract lineage dicts (mock engine / legacy HTTP engine) return
    None → the legacy majority-vote `agreement_fraction` path is
    unchanged, so every pre-existing pipeline test passes untouched.
- `_stage_lineage_analysis` calls it after spend recording; a
  `RigorConfigError` (corrupt bundled rigor config) maps to
  `StageUnrecoverableError`, mirroring the battery-config handling
  (fail closed, FR-004 taxonomy). When rigor is computed, its
  `agreement_fraction` supersedes the majority-vote fraction in the
  stage output and the `cases` row (the uncertainty stage consumes the
  §3.3 metric), and a `rigor_evaluated` audit event records runtime,
  pass/fail, conditions, and gate reasons. The rigor result is persisted
  in the `lineage_analysis` stage output (`"rigor"` key, null on the
  legacy/skipped paths) so the resume path feeds the verdict stage.
- `_stage_verdict` passes the persisted rigor into `synthesize_verdict`;
  `verdict_set` audit detail now includes the qualification list.
- `config.py` (supporting file): `UAPV_LINEAGE_RUNTIME` key (spec §4,
  default `live`) + `Settings.lineage_runtime` property; unknown values
  fail closed to `live` (the strictest runtime).

### 2. Verdict consumption of rigor + coverage (`verdict.py`)

Spec §2 row: "Consume rigor + coverage; qualified no_mundane_match;
astronomical qualification." `synthesize_verdict` gains optional
`rigor_result` / `coverage` inputs and post-processes the FR-007 rule
output (the rule itself is untouched):

- **Astronomical (OBL-10):** lineages classify `astronomical` (new
  taxonomy; legacy `astronomical_object` accepted) AND the astronomical
  battery category ran negative — the current-battery form of the spec's
  `no_mundane_match_astronomical_checked` — on a `no_mundane_match`
  verdict → `astronomical_classification_present: true`, the spec-literal
  note (shared constant with `finalize_rigor`), and an
  `astronomical_classification_present` qualification. A positive
  astronomical adapter still yields `mundane_identified` — no
  false-unresolved gap.
- **Coverage (§3.5):** weather/balloon `geographic_coverage`
  sparse/unavailable → the spec-literal caution note + `coverage_sparse`
  qualification. The coverage_check stage that produces this summary is
  staged with the 11-category battery; the consumer is wired (pipeline
  verdict stage passes `battery.coverage` through to `synthesize_verdict`)
  and unit tested now.
- **Rigor:** a supplied rigor result that did not pass →
  `rigor_not_certified` qualification and a provisional note carrying
  the rigor reason (includes mock's `runtime_blocked`); a passed result
  leaves the exclusion conclusion unqualified. Qualifications apply to
  `no_mundane_match` only — mundane_identified/insufficient_data remain
  battery-driven per FR-007.
- All verdict dicts now carry `astronomical_classification_present`,
  `qualifications`, `rigor_passed` (None when no rigor was evaluated),
  and `rigor`.

### Tests (+18, all green: 332 passed)

- `test_verdict.py` (+6): astronomical qualification present/absent
  (positive adapter → mundane, no qualification), rigor-not-certified
  provisional NMM, rigor-passed unqualified NMM, coverage-sparse
  qualification, and no qualifications on mundane verdicts.
- `test_pipeline.py` (+12): call-site legacy-dict/None paths; live
  fail-closed without operator artifacts (gate reasons asserted); live
  pass with certified artifacts (reuses test_rigor's fixture writers so
  the pipeline exercises exactly the documents `lineages validate` /
  `lineages calibrate` produce); simulation [1,2,6]; mock blocked;
  unknown runtime fails closed to live; stage wiring (rigor in output,
  concordance supersedes majority vote in stage output + cases row,
  `rigor_evaluated` audit, certified pass, legacy path unchanged with no
  rigor event); e2e verdict qualification — blocked rigor → provisional
  `no_mundane_match` persisted + `rigor_not_certified` in the
  `verdict_set` audit, certified rigor → unqualified.

### Staged items (reviewer-sequenced, unchanged)

l1–l7 implementations, `tests/fixtures/simulation_fixtures.json`,
sandbox + import guard, 12-stage pipeline rebuild (rigor_adjudication /
coverage_check stages, `rigor_runs` persistence of the final rigor via
`finalize_rigor`), 11-category battery (coverage producers). The
preliminary concordance is stored in the stage output until
`rigor_runs` lands with the migration round.

## Round 4 (2026-08-21)

Reviewer round-3 verdict: ACCEPTED, no blockers. Round-4 scope is the
reviewer's FOCUS_NEXT: `calibration.py`, the calibration half of
`eval_set.py`, and rigor conditions 4/5 wiring.

### 1. Calibration eval-set loader (`eval_set.py`)

The spec's §2 role for `eval_set.py` is "calibration (≥100 items,
l6-enriched ≥60 in covered categories) and independence (≥300 items…)";
round 3 landed the independence half, this round lands calibration:

- Operator set at `<var_dir>/operator/eval_set/manifest.json` (runtime
  unification of `tests/fixtures/eval_set/`, mirroring the independence
  path decision). Item schema: `item_id`, `subset` (general /
  l6_enriched), `label` (ground truth — required, taxonomy-valid, never
  null: accuracy needs ground truth), `classifications` keyed by exactly
  the seven declared lineage ids (taxonomy-valid or null), and
  `confidences` (raw confidence per recorded classification; required
  channel — the Platt/isotonic fits consume it; null classification <->
  null confidence enforced).
- Composition fail-closed: ≥100 items; ≥60 items labeled in the
  l6-covered categories (`aircraft`, `bird`, `insect` — spec §3.2's
  ImageNet-1k coverage); `l6_enriched` items must carry a covered label.
- Absent operator set → deterministic 20-item synthetic fallback marked
  `synthetic_ci_not_production_calibration` (mirrors §3.2 pt 8's
  fail-closed philosophy for the independence set: 20 < 80 comparable
  minimum, so no lineage can ever certify from it).
  `fallback_to_synthetic=False` for live-mode gating.
- Stricter than the round-3 independence loader on the `"eval_set"` kind
  key: calibration requires it literally (`"calibration"`), no default —
  a mis-pointed independence manifest is rejected. The independence
  loader's default-to-`"independence"` laxity (reviewer round-3 NOTE) is
  left untouched as approved behavior; both manifest schemas should be
  documented in `docs/OPERATIONS.md` when the eval-set docs land.
- `validation_set_hash` (SHA-256 of manifest bytes; canonical-JSON hash
  for synthetic sets) feeds the `LineageOutput.calibration` block's
  `validation_set_hash` field.

### 2. `calibration.py` (new)

Spec §3.2 calibration rules, pure stdlib:

- Accuracy over comparable items only (recorded classification non-null;
  abstentions excluded and reported as `abstention_rate`). <80
  comparable → fail with the spec-literal
  `insufficient_comparable_for_calibration`, no parameters fitted.
  `calibrated: true` ⇔ accuracy ≥ 0.60 AND ≥80 comparable (thresholds
  read from `rigor_config.json`: `calibration_accuracy_threshold`,
  `calibration_min_comparable`; boundary 0.60 passes, tested).
- Fits per spec: Platt scaling (temperature via deterministic
  golden-section NLL minimisation) for l6; isotonic regression (pool
  adjacent violators, midpoint-knot linear interpolation) for l1–l5, l7.
  `apply_calibration()` is monotone, maps into [0,1], rejects unknown
  parameter shapes.
- `calibrate_lineage()` / `calibrate_lineages(registry, eval_set)` —
  registry supplies ids in declaration order; recorded data drives the
  statistics (same honest-unavailability contract as round-3
  independence: unavailable implementations are annotated in the CLI
  summary, never silently skipped).
- `CalibrationResult.to_dict()` is the stored parameter file;
  `.calibration_record()` emits the exact `LineageOutput.calibration`
  block shape (calibrated/source/validation_set_hash/accuracy/precision/
  recall/abstention_rate/comparable_count). Precision/recall are
  macro-averaged per-category over comparable items.

### 3. CLI `lineages calibrate` (OBL-21)

Loads the calibration eval set (synthetic fallback when absent), runs
`calibrate_lineages`, stores `var/lineage_calibration/{lineage_id}.json`
for all 7 lineages, prints a JSON summary (all_calibrated, provenance,
failed lineages, unavailable implementations, validation_set_hash),
exits 1 if any lineage fails. Malformed operator manifest → exit 1 with
no parameter files written (fail closed, same as `lineages validate`).

### 4. Rigor conditions 4/5 wiring (`rigor.py`)

Implements the reviewer's round-3 NOTE verbatim: the stored report is
validated against `rigor_config.json` protocol parameters, never trusted.

- `validate_stored_independence_report(var_dir)`: rejects missing /
  invalid-JSON reports; rejects the synthetic marker and any non-operator
  eval-set source; re-checks `n_resamples >=
  independence_bootstrap_samples` (10000), `ci_level ==
  independence_ci_level` (0.995), `min_comparable >=
  independence_min_comparable_items` (100) — a 50-resample smoke run
  stored at the canonical path cannot satisfy live rigor; requires all 21
  pairs and re-derives each pair's outcome from raw `ci_upper <
  independence_threshold` and comparable counts (a hand-edited
  `"passed": true` with `ci_upper >= 0.2` still fails).
- `validate_stored_calibration(var_dir)`: for every declared lineage,
  requires `var/lineage_calibration/{id}.json` with matching lineage_id,
  operator provenance (synthetic marker rejected), `calibrated: true`,
  accuracy ≥ `calibration_accuracy_threshold`, comparable_count ≥
  `calibration_min_comparable`.
- `evaluate_operator_gates(var_dir) -> OperatorGates`: derives the
  condition 4/5 booleans (fail-closed False when artifacts are absent —
  spec §3.2 pt 8's "false, not operator_gated") for live-mode callers to
  pass into `compute_concordance(..., independence_validated=...,
  calibration_valid=...)`. `compute_concordance` stays pure/boolean —
  the pipeline-integration round will call `evaluate_operator_gates` on
  the live path (pipeline/verdict don't invoke concordance yet; that's
  the staged l1–l7 + 12-stage pipeline work).

### Tests (+43, all green: 314 passed)

- `test_lineages.py` (+17): calibration loader operator/synthetic/
  composition/malformed paths; pass/insufficient/low-accuracy/boundary
  calibration; Platt-vs-isotonic method dispatch; synthetic set fails
  closed for all 7; PAV violator pooling; `apply_calibration` monotonicity
  + rejection paths; LineageOutput-block shape; macro precision/recall.
- `test_cli.py` (+3): calibrate synthetic fail-closed, operator pass with
  7 stored parameter files + method assertions, bad manifest fail-closed
  with no files written.
- `test_rigor.py` (+23): condition 4 rejects missing/smoke-run/wrong-CI/
  lowered-comparable/synthetic/no-provenance/pair-count/failing-pair/
  insufficient-pair-comparable reports and accepts valid ones; condition
  5 rejects missing/low-accuracy/low-comparable/uncalibrated/synthetic/
  mismatched records and accepts valid ones; `evaluate_operator_gates`
  empty-var fail-closed, fully-certified pass, and end-to-end live
  compute_concordance with gates (ALRC path) + smoke-run blocking.

### Staged items (reviewer-acknowledged, not re-blocked)

l1–l7 implementations, `lineages calibrate` consumers inside the
pipeline, rigor condition 4/5 wiring at the live pipeline call site,
12-stage pipeline, sandbox. Calibration loader now complete per the
reviewer's round-3 NOTE so it ships with this round rather than the
calibration one.

## Round 3 (2026-08-21)

### Carried blocker re-verified, not re-fixed

The round-3 dispatch still carried the round-1 contract-deviation blocker
(per-pair abstention rate + difficulty channel). That fix landed in round 2
and is present in the worktree verbatim at the lines the reviewer ACKed:
`independence.py` `item_difficulties` channel (line 116), per-pair
`abstention_rate`/`excluded`/`total_items` (210, 220-222), per-pair and
report-level `difficulty_distribution` (211-215, 223, 273), length-mismatch
`ValueError` (165-169), plus the four covering tests in
`tests/test_lineages.py`. DISPUTED as stale with this evidence rather than
re-"fixed".

### Spec-shaped signature + eval_set.py + CLI (the committed round-3 work)

Closes the reviewer's round-2 NOTE (spec-shaped public signature pending)
and the round-2 commitment recorded below.

1. **`src/uapvf/lineages/eval_set.py` (new).** Independence eval-set
   management per §3.2 pts 1/7/8:
   - Operator set lives at `<var_dir>/operator/eval_set_independence/
     manifest.json` (unified runtime path per spec §2). Item schema:
     `item_id`, `subset` (general/l6_enriched/l4_enriched/l7_enriched),
     `difficulty` (hard/ambiguous/easy for general items), and
     `classifications` keyed by exactly the seven declared lineage ids with
     taxonomy-valid values or `null` (an abstention is recorded data, never
     a missing key — the loader rejects both unknown and missing lineage
     keys).
   - Composition validated fail-closed: ≥300 items, subset minimums
     100/100/50/50, ≥30% hard/ambiguous among general items (exact integer
     arithmetic: `hard*10 >= 3*general_total`). Subset counts are minimums,
     not exact values, so larger supersets are permitted.
   - Absent operator set → deterministic 20-item synthetic fallback marked
     `synthetic_ci_not_production_independence` (§3.2 pt 8). The difficulty
     channel carries difficulty labels for general items and subset tags for
     enriched items, so the ≥30%-hard composition and enriched-subset sizes
     stay auditable in the stored report. `fallback_to_synthetic=False`
     lets live-mode callers fail closed on missing operator data.
2. **`independence.py`: spec-shaped `validate_independence(registry,
   independence_eval_set)`** as a first-class call form of the same public
   function (dispatch on the eval-set duck type). Lineage ids come from the
   registry in declaration order; classifications + difficulty channel come
   from the eval set; the report gains `registry` (ids + unavailable
   implementations — honest, not a skip) and `eval_set` provenance blocks
   (source, marker, path, item count, subset counts). Mixing
   `lineage_ids`/`item_difficulties` with an eval set is rejected; empty or
   malformed registries raise. The dict-based statistics core is unchanged
   (still the legacy first-positional form, `lineage_ids` still accepted as
   second positional), so all round-2 tests pass untouched.
3. **`cli.py`: `uapvf lineages validate` (OBL-21) and `uapvf lineages
   list` (AC-2).** validate loads the registry + eval set, runs the
   spec-shaped validation, stores the report at
   `var/lineage_independence_report.json` (written even when pairs fail,
   per spec; NOT written when the operator manifest is malformed —
   fail-closed exit 1 with the error on stderr), prints a JSON summary, and
   exits 0 iff all pairs pass. Synthetic fallback therefore exits 1 (20
   items < 100 comparable). `--resamples`/`--seed` flags exist only for
   smoke runs; defaults remain the spec protocol (10000 resamples, module
   seed constant). Neither command requires the operator token (local
   certification ops over var/, like `db init`; no case data or DB
   touched).

Tests: 9 new in `tests/test_lineages.py` (operator manifest load, fallback
+ opt-out, composition rejections, malformed-item rejections, synthetic
fail-closed, spec-shape pass over a 560-item operator set with
provenance/difficulty assertions, conflicting-channel and bad-registry
rejections) and 4 new in `tests/test_cli.py` (list prints 7, synthetic
fallback exit 1 + marked report, operator set exit 0 + clean report,
malformed manifest exit 1 with no report). Full impact gate: 271 passed.

Finalizer closure: `lineages calibrate`, all seven concrete lineage modules,
stored calibration/independence consumers, and rigor conditions 4/5 are now
implemented. Production defaults are strict/live; `SNEFERU_MOCK=1` remains
only in isolated tests.

## Round 2 (2026-08-21)

### Independence report contract (spec §3.2 pt 6/7, AC-2) — blocker closed

`src/uapvf/lineages/independence.py::validate_independence` now reports
everything §3.2 pt 6 mandates and accepts a difficulty channel:

- **New input channel:** `item_difficulties: Optional[Sequence[Optional[str]]]`,
  a parallel list with one difficulty label per eval item (e.g. `"hard"`,
  `"ambiguous"`, `"easy"`, `"l6_enriched"`). Chosen as a parallel list rather
  than a reserved key inside the item dicts so difficulty labels can never
  collide with lineage-id keys and the auto-detected `lineage_ids` scan stays
  untouched. Length mismatch raises `ValueError` (input validation before
  business logic). `None` entries count as `"unlabeled"`.
- **Per-pair report fields added:** `total_items`, `excluded`,
  `abstention_rate` (= excluded / total_items; AC-2 "Per-pair abstention
  rates reported"), and `difficulty_distribution` (counts over that pair's
  comparable items only — abstained items are excluded per pair, matching the
  κ computation).
- **Report-level `difficulty_distribution`** counts over all eval items, so
  the l4/l6/l7-enriched subset sizes and the ≥30% hard/ambiguous general-item
  composition (§3.2 pt 7) are auditable from the stored report.
- Backward compatible: all pre-existing call sites pass keyword args; with no
  difficulty channel supplied, distribution fields are `None`.

Tests: `tests/test_lineages.py` extended — abstention test now asserts
per-pair `abstention_rate`/`excluded`/`total_items`; three new tests cover
report-level + per-pair difficulty distribution, abstained-item exclusion in
per-pair distributions, and length-mismatch rejection.

### Registry signatures (reviewer question, round 1)

`registry.load_registry()` returning `RegistryEntry` wrappers (availability
tracking, fail-closed) and `validate_independence` taking plain classification
dicts are deliberate intermediate contracts. The registry-driving adapters
(`uapvf lineages validate` / `calibrate` per OBL-21) now load the registry,
load the registry, load the independence eval set (classifications +
difficulty labels) from `var/operator/eval_set_independence/`, call
`validate_independence()`, and write `var/lineage_independence_report.json`.
The public signatures reconcile with the spec's
`validate_independence(registry, independence_eval_set)` shape via a thin
adapter; the pure statistics core stays dict-based for testability.

### Documentation closure

Production and operator documentation now use strict/live defaults and the
explicit `UAPV_LINEAGE_RUNTIME`; the test plan names simulation separately
from deterministic mock fixtures.

### Audit note added

`src/uapvf/defaults/evidence_vocabulary.json` description now records the spec
§3.2 text bug ("l1: … = 8" vs four allowed shared claims #1/#2/#3/#6) and
that the normative allowed-emitters table governs.
