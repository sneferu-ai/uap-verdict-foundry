# CONTRACT.md — UAP Verdict Foundry (B13 MVP slice)

Operator-readable contract: what this service does, promises, and
refuses. Maintained per round; drift from code is a P1 finding.
Round 3 scope changes: schedulers removed, lineage analysis re-routed
through the Sneferu engine adapter, readyz restored to S8, CLI surface
trimmed (`retention run` / `canon` removed, `tle refresh` added).

## 1. Purpose

Accepts one UAP media file + capture time/location/MUFON-style fields,
runs an autonomous multi-lineage vision assessment (Sneferu engine,
FR-005) and a closed-set mundane-explanation battery (FR-006), and
returns a provenance-stamped, size-capped tri-state verdict document
(`no_mundane_match` / `mundane_identified` / `insufficient_data`) with
calibrated uncertainty. Unresolved cases additionally emit a TEXT-ONLY,
permanently labelled speculative fiction seed. The operator exports and
delivers reports manually. No human analyst review at any stage.

## 2. Endpoints (surface highlights)

| METHOD · path | auth | notes |
|---|---|---|
| GET /healthz | public | `{"status":"ok"}` liveness |
| GET /readyz | public | S8 contract: 503 ONLY for DB-write-probe failure, SDK-unreachable-in-live-mode, or missing ffmpeg. Archive/ADS-B/TLE are reported fields, never gates |
| POST /api/v1/cases | bearer | intake (JSON or multipart), validation before row insert |
| GET /api/v1/cases[/{id}] | bearer | case state; S2 retention reminder badge via `retention_exceeded` |
| GET/POST /cases/{id}/delete[/confirm] | session+CSRF | S11 hard delete (FR-021 via `retention.purge_case`) |
| GET /cases/{id}/report.{html,json} | session | size-capped verdict artifacts (≤1.5 MB / ≤500 KB) |
| /login /logout /terms | public/session | S9–S12 |

REMOVED surfaces (must 404): buyer-facing delivery (`/d/{token}`,
delivery asset/issue/revoke routes) — phase 2; xenoscience and
story-asset stages — deferred. NO unauthenticated content route exists.

## 3. Persistent state

SQLite (`var/uapvf.db`): `cases`, `lineage_outputs`, `battery_results`,
`pipeline_stage_runs`, `spend_entries`, `audit_events` (hash-chained,
indefinite retention, survives case deletion — no FK on case_id),
`legal_holds`, `purge_runs`, `tle_catalogs`, `sessions`, benchmark
tables. Round 4 removed ALL deferred-feature DDL (delivery_tokens,
delivery_events, xenoscience_runs, story_assets, canon_nodes,
canon_edges): nothing reads or writes them, and pre-creating tables for
removed phase-2 surfaces made the schema lie about the product.
Existing databases keep any already-created tables inertly; nothing
references them. Filesystem:
`var/cases/{id}/` (normalized media, report.html/json, `.fic.md`),
`var/tle/` (manually refreshed catalogs), `var/retention/` (purge
proofs), `var/backup/`.

## 4. Trust boundaries

Operator token (`.env` UAPV_OPERATOR_TOKEN) gates web session, CLI
possession check (`hmac.compare_digest`), and API bearer. All case
routes authed; only /healthz, /readyz, /login, /terms public. NO
buyer-facing surface in MVP (spec §1 OUT). Secrets never logged.
Engine calls go only to SNEFERU_SDK_URL via the adapter.

## 5. Failure modes (fail-closed)

- Engine unreachable/timeout on lineage submission → transient retry
  3×, then case `failed` with named reason (never mock fallback, never
  fabricated lineages — A-013).
- Engine fiction generation fails (timeout/5xx/transport, then retry
  once per FR-011) → deterministic labelled fallback formatter; live
  fallback seeds book NO spend (FR-016: SDK calls only). Selecting the
  removed `simulation` runtime (UAPV_LINEAGE_RUNTIME=simulation) fails
  closed with a named error — never silent live-engine spend.
- Engine answers out-of-schema/out-of-enum → case `failed` immediately
  (unrecoverable).
- Stale/absent TLE catalog → satellites category `insufficient`
  (A-012), never invented negative.
- NO auto-deletion anywhere: retention is manual operator action only
  (spec §5); NO automatic TLE refresh: `uapvf tle refresh` is a
  one-shot operator command (phase 2 owns the scheduler).
- DB write probe failure / ffmpeg missing / live-SDK-unreachable →
  readyz 503; every other dependency is a reported readiness field.

## 6. Idempotency + replay

Intake re-submission creates distinct cases; `case retry` reuses
run_version, `case rerun` increments it; stage outputs persist per
run_version and crash recovery re-executes only uncommitted stages
(duplicate external spend recorded `duplicate_call_recovery:*`).

## 7. Observability

Hash-chained audit events for every stage transition, spend entry,
verdict, rigor evaluation, delete/purge, legal hold, and operator
action. Mock-mode engine calls logged to `var/mock_call_log.jsonl` with
call ids. `/metrics` low-cardinality only (no case ids).

## 8. Migrations

None in MVP (`uapvf db init`, idempotent; additive column upgrades via
`_additive_upgrade`). Round 4 deleted the deferred-feature DDL; existing
databases keep any such tables inert (never referenced).

## 9. Configuration

`.env`: UAPV_OPERATOR_TOKEN (required); SNEFERU_MOCK (default 0 in
shipped template, tests opt into 1; the ONLY non-live mode — spec's
two-mode model); optional ARCHIVE_MIRROR_PATH,
ADSB_SOURCE_URL(+credentials), TLE_CATALOG_URL, cost overrides,
UAPV_SPEND_CAP_USD (200), RETENTION_DAYS (365, reminder badge only).
Removed knobs: UAPV_CANON_ENABLED, UAPV_TLE_REFRESH_HOURS,
UAPV_RETENTION_INTERVAL_HOURS, UAPV_COST_XENOSCIENCE_USD.
UAPV_LINEAGE_RUNTIME accepts only live|mock; `simulation` was removed
and selecting it raises LineageRuntimeRemovedError (fail closed).

## 10. Threat model

Mitigated: unauthenticated content access (no public case/delivery
routes); hostile media decode (case-scoped subprocess decode with
network denial where the host supports it); audit tampering
(hash-chained events, indefinite retention, deletion survives audit).
Out of scope (operator/upstream): engine-side model security (A-013),
full subprocess isolation for all media processing (phase 2), payment
settlement (off-platform by design).
