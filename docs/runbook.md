# UAP Verdict Foundry — Operator Runbook

## First-time setup

1. `python3 -m pip install -r requirements.lock && python3 -m pip install -e .`
2. `cp .env.example .env` — set `UAPV_OPERATOR_TOKEN` to a long random string.
3. Ensure `ffmpeg`/`ffprobe` are on PATH (video support).
4. `uapvf db init`
5. `uapvf references fetch` — must report every frozen source verified.
6. `uapvf serve` → open http://127.0.0.1:8470, log in with the token.

Fresh-install defaults are `SNEFERU_MOCK=1` and `UAPV_REFERENCE_MODE=degraded`
so the smallest deploy works without live credentials or fetched references.
For production, switch to `SNEFERU_MOCK=0` and `UAPV_REFERENCE_MODE=strict`,
then run `uapvf references fetch` before accepting cases. The former
`simulation` runtime was removed: it silently spent live-engine money under a
"simulation" label, and selecting it now fails closed with a named error.

## Daily operations

- **Submit**: web form (`/cases/new`) or
  `uapvf case submit --media FILE --fields fields.json`. The estimate shown
  at intake is an upper bound (lineage ×3 + fiction, because the verdict is
  unknown at intake).
- **Watch progress**: case detail page auto-refreshs while analyzing;
  `uapvf case status <id>` shows stage history.
- **Export** (the durable forensic record): `uapvf case export <id> --out DIR`
  — verifies `report.html`/`report.json`/fiction hashes before copying.
  Exits 1 on any hash mismatch.
- **Delivery (MVP)**: the operator delivers manually. Export with
  `uapvf case export <id> --out DIR` (above) and send the report by forum
  post, email, etc. There is no buyer-facing delivery surface in the MVP —
  token-gated delivery is deferred to phase 2 (spec.md:84/92).
- **Fiction only**: `uapvf case fiction <id> --out DIR` (resolved cases have
  no seed; the command says so and exits 1).
- **Retry a failed case**: web button or `uapvf case retry <id>`
  (≤5 retries; `--force` resets the counter and overrides).
- **Rerun a complete case**: web button or `uapvf case rerun <id>` — new
  run_version, old report archived as `report_<id>_v<N>.html`, old verdict
  audited as superseded.
- **Payment status**: case page dropdown or
  `uapvf case set-payment <id> --status paid|unpaid|comped` (audited).
- **Retention / Delete**: there is NO auto-deletion in the MVP (spec §5).
  Cases older than `RETENTION_DAYS` show an informational dashboard
  badge; deletion is always operator-initiated: case page → confirm page
  → permanent, or `uapvf case delete <id>`. Hard delete removes the row,
  all results, and all case files, scrubs pre-existing backups, and
  performs a disposable restore to prove the case is absent. Audit
  entries survive; spend rows keep their cost with a NULL case reference.
  Place exceptional cases on legal hold with
  `uapvf retention hold <id> --reason ...` (blocks deletion) and release
  explicitly with `uapvf retention release <id>`.
- **Orbital catalog (satellites category)**: refresh is MANUAL (A-012) —
  `uapvf tle refresh` performs one operator-initiated fetch of
  `TLE_CATALOG_URL`. A stale (>7 days) or absent catalog makes the
  satellites category `insufficient`, never an invented negative. There
  is no background refresh scheduler in the MVP.

## Speculative canon

The canon continuity graph is deferred to post-launch (spec §1 deferred
table) and is not part of the MVP; no canon commands ship in this slice.

## Spend control

- Cap: `UAPV_SPEND_CAP_USD` (default 200/month, global). Cases over cap are
  created as `spend_capped` (never analyzed) and automatically requeue when
  the month's total allows. In-flight cases are never interrupted.
- Benchmark seed cases (`buyer_ref = __benchmark_seed__`) are exempt from
  both the cap and the 20/hour rate limit, and accrue no spend entries.
- Summary: `uapvf spend` or `GET /api/v1/spend` (bearer token).

## Benchmark & calibration

```bash
uapvf benchmark prereg   # canonicalize + hash var/benchmark/prereg.json
uapvf benchmark run      # requires the server; runs 10 seeds end-to-end
uapvf benchmark results
```

Thresholds are preregistered: editing `prereg.json` changes the hash, and
each benchmark run records which hash it was judged against. Calibration
written by a **live-mode** run is what live-case uncertainty consumes; mock
calibration never applies to real cases.

## Audit

- `uapvf audit <case_id>` — full event list for one case.
- `uapvf audit verify --all` — recompute the whole hash chain (exit 1 on any
  mismatch or break; a clean restore keeps the chain valid end to end — the
  `restore_genesis` continuation entry re-links to the old tail, so
  `fork_points` stays empty unless entries were actually tampered with or
  truncated).

## Backup & restore

- `uapvf backup` → consistent SQLite snapshot (online backup API) +
  `var/cases/` (excluding in-flight case directories) + operator config +
  benchmark tree, packed into `var/backup/uapvf_backup_<ts>.tar.gz`.
- `uapvf restore <tarball>` — SIGTERMs a running server (waits ≤30 s via
  `var/server.pid`), unpacks over `var/`, reconciles: analyzing cases with
  intact artifacts requeue; analyzing cases whose artifacts were excluded
  from the backup fail loudly with a re-submit instruction. A restore writes
  a genesis-continuation audit entry (`restore_genesis`) so the chain stays
  verifiable end to end.

## Restart behavior

A crash or restart never loses committed work: `analyzing` cases reset to
`queued` at startup and resume from their committed stage outputs; orphaned
`*.tmp` files are cleaned; expired sessions are purged.

## Failure triage

- `failed` case → open the case page: `error_detail` names the stage and
  reason; the audit log carries the stage-level events. Transient failures
  already retried 3× before failing.
- Battery configuration problems are unrecoverable by design (a misconfigured
  category must not silently skip): fix `var/battery_config.yaml`, then
  `uapvf case retry <id>`.
- `spend_capped` → raise the cap or wait for the UTC month rollover.
- Readiness probe failures (`/readyz`): `db not ok`, `ffmpeg missing`, or
  (live mode) `sdk unreachable` are reported with 503 + reason.
