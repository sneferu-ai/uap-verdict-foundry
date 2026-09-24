# UAP Verdict Foundry — Test Plan

The automated suite under `tests/` covers deterministic fixtures plus real
ffmpeg normalization, case-sandbox boundaries, retention,
references, and TLE failure modes. Run with
`python3 -m pytest tests/ -q`.

## Coverage map (spec acceptance criteria -> test file)

| Area | AC / FR | Tests |
|---|---|---|
| Terms gate (no row without acceptance) | AC-001, FR-001 | `test_intake.py::TestTermsAcceptance` |
| Media MIME/size/decodability rejection | FR-001, FR-017 | `test_intake.py::TestMediaRejection` |
| Field validation (ISO-8601 offset, ranges, notes cap) | FR-024 | `test_intake.py::TestFieldValidation` |
| Rate limit 20/hour + benchmark exemption | FR-016 | `test_intake.py::TestRateLimit` |
| Spend cap + exemption + monthly window | FR-016 | `test_intake.py::TestSpendCap`, `test_pipeline.py::TestSpendCapLifecycle` |
| Quality gate floor + configurability | AC-002, FR-002 | `test_battery.py::TestQualityGate` |
| ADS-B match/no-match/coverage/forced-positive | AC-003, FR-006 | `test_battery.py::TestBatteryAircraft` |
| Satellite pass/no-direction/stale-TLE | FR-006 | `test_battery.py::TestBatterySatellites` |
| Archive match/magnitude filter/no-catalog | AC-023 | `test_battery.py::TestBatteryAstronomical` |
| Lineage zero/one/artifact-threshold | FR-005 | `test_battery.py::TestBatteryLineages` |
| Battery config errors unrecoverable | FR-006 | `test_battery.py::TestBatteryConfig` |
| Tri-state verdict rule + drone amendment | AC-004, FR-007 | `test_verdict.py::TestVerdictRule` |
| No-claims language | AC-006 | `test_verdict.py::test_no_extraterrestrial_claims_language` |
| Uncertainty penalties + calibration bins | AC-015, FR-008 | `test_verdict.py::TestUncertainty` |
| Full lifecycle aircraft/nmm/insufficient | AC-004 | `test_pipeline.py::TestFullLifecycle` |
| Failure -> retry; mock fail hook | AC-010, FR-018 | `test_pipeline.py::TestFailureAndRetry` |
| Restart resume + corrupt stage re-run | AC-012, AC-013, §3 | `test_pipeline.py::TestFailureAndRetry`, `TestRestartContract` |
| Rerun version bump/archive/supersede | AC-026..AC-029 | `test_pipeline.py::TestRerun` |
| Hard delete + audit survival | AC-022 | `test_pipeline.py::TestOperatorActions`, `test_audit.py` |
| Payment transitions audited | AC-024 | `test_pipeline.py::TestOperatorActions` |
| Audit hash chain + tamper detection | AC-009, FR-015 | `test_audit.py` |
| Benchmark metrics + prereg hash | AC-016, FR-014 | `test_benchmark.py` |
| Fiction layout + retry ladder + clearing | AC-025, FR-011/FR-012 | `test_render.py::TestFiction*` |
| Report content + linter + size caps | AC-020, FR-009 | `test_render.py::TestReportContent` |
| Auth: bearer/cookie/CSRF/logout | AC-005, AC-030 | `test_api.py::TestAuth`, `TestCaseActionsWeb` |
| API intake/list/downloads/spend | S7 API | `test_api.py::TestApiIntake`, `TestDownloads` |
| No-JavaScript pages | §2 | `test_api.py::test_no_javascript_in_pages` |
| CLI commands + backup/restore | S7 | `test_cli.py` |
| Lineage taxonomy/protocol/vocabulary/registry/frame sampling/independence/calibration | §3.2 | `test_lineages.py` |
| Rigor engine: concordance, refutation, adjudication, finalization, operator certification gates | §3.3 | `test_rigor.py` |
| Seven case-sandboxed lineage implementations + network/cross-case denial | Phase 2 | `test_media_sandbox.py`, `test_lineages.py` |
| Signed buyer delivery, precedence, expiry, revocation, token secrecy | Phase 2 | `test_delivery.py` |
| Eleven TTLs, legal hold, backup scrub + actual restore proof | Phase 2 | `test_retention.py` |
| Frozen controlling-reference gate | Phase 2 | `test_references.py` |
| Scheduled TLE substantive-change/staleness behavior | Phase 2 | `test_tle.py` |
| Optional non-evidence canon activation/deactivation/projection | Phase 2 | `test_canon.py` |

## Browser smoke plan (runtime verification)

Spawn the server in explicit simulation mode with a free port, then drive the primary
operator flow with a browser automation tool (Playwright) or a manual
equivalent. Verify each observable:

1. **Login** — `GET /login` returns a form. Submit the operator token and
   receive a session cookie + 302 redirect to `/cases`.
2. **Intake** — `GET /cases/new` shows the static cost estimate, terms text,
   and a multipart form. Upload `tests/fixtures/unexplained.jpg` with valid
   capture time/place/fields, check terms, submit. Receive 303 redirect to
   `/cases/{case_id}`.
3. **Detail progress** — `/cases/{case_id}` shows status `analyzing` with a
   stage log, then auto-refreshes to `complete`. The verdict is
   `insufficient_data`, lineage rows show findings or honest abstention,
   and the report download controls are present.
4. **Report download** — `GET /cases/{case_id}/report` returns `report.html`
   with the disclaimer header, the evidence/interpretation boundary rule, and
   no banned ET-origin assertion phrases outside negated interpretation
   context. The JSON sidecar is ≤ 500 KB.
5. **Fiction download** — `GET /cases/{case_id}/fiction` returns a `.fic.md`
   file whose every paragraph begins with the required non-evidence prefix.
6. **Case list** — `/cases` shows the new case row with status, verdict, and
   payment status; the total count header matches.
7. **Export and deliver (MVP)** — `uapvf case export <id> --out DIR`
   re-verifies report/JSON/fiction SHA-256 hashes and copies the files;
   the operator delivers manually. The buyer-facing token delivery surface
   is deferred to phase 2 (spec.md:84/92) — no `/d/{token}` route exists.
8. **Payment update** — Submit the payment form on the detail page; the list
   and detail pages reflect the new status and an audit event is recorded.
9. **Logout** — `POST /logout` clears the session cookie and redirects to
   `/login`; subsequent `/cases` requests redirect to login.

The demo server is started with `SNEFERU_MOCK=0`,
`UAPV_LINEAGE_RUNTIME=simulation`, verified frozen references, and an isolated
`UAPV_VAR_DIR`. The UI and reports label the runtime; no live-certification
claim is made.

## Manual operator checks (not automated)

- A user-supplied real video beyond the automated ffmpeg MP4 normalization test.
- Live-mode adapter behavior (requires Sneferu engine + data sources; A-013).
- Real-browser review of the web pages.
