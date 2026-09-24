# UAP Verdict Foundry — API Reference

Every HTTP route is declared in `src/uapvf/server.py`; the CLI is defined in
`src/uapvf/cli.py`. Nothing beyond what is listed here exists. Base URL for
local development: `http://127.0.0.1:8470` (`uapvf serve --port` changes it).

## 1. Authentication model

One secret: `UAPV_OPERATOR_TOKEN` (from `.env`). Three shapes, constant-time
compared (`hmac.compare_digest`) everywhere:

| Surface | Mechanism | Where |
|---|---|---|
| Browser (HTML pages + SPA JSON dialect) | `POST /login` with form field `token` → sets cookie `uapv_session` (base64 session id + HMAC-SHA256 over it, keyed by the operator token). Sessions are 256-bit, TTL 12 h, stored in the `sessions` table. Mutating forms require the per-session `csrf_token`. | `/cases*`, `/login`, `/logout`, `/benchmark`, `/terms` |
| HTTP API | `Authorization: Bearer <UAPV_OPERATOR_TOKEN>` | `/api/v1/*`, `POST /benchmark/run` |
| CLI | Local possession check: `UAPV_CLI_TOKEN` env (or interactive prompt) compared against the `.env` token. `UAPV_ALLOW_LOCAL_ADMIN=1` bypasses (audit-warned). | all case/audit/spend/backup commands |

Unauthenticated web requests redirect `302 → /login`; unauthenticated
`/api/v1` requests return `401 {"detail":"unauthorized"}`.

**Content negotiation.** `/login`, `/logout`, `/terms`, `/cases`,
`/cases/{id}*`, `/benchmark`, `/benchmark/run` are negotiated: they return
JSON when `Accept: application/json` outranks `text/html`
(`web_json.wants_json`); absent or `*/*` Accept gets HTML. All negotiated
responses carry `Vary: Accept`.

**JSON error envelope** (negotiated routes): `{"error": "...", "code": "...",
"ok": false}`. Typed codes (consumed by the SPA, `ui/src/lib/types.ts`):
`auth_failed` (401 bad login), `auth_expired` (401 session gone),
`csrf_expired` (403), `spend_cap_reached` (403), `conflict` (409),
`validation_error` (422, with `errors: [{field, message}]`),
`file_too_large` (413), `unsupported_type` (415), `rate_limited` (429),
`insufficient_storage` (507), `bad_request` (400), `not_found` (404).
`/api/v1` routes use FastAPI-style `{"detail": "..."}` instead.

**Rate limits / caps.** Intake is limited to `UAPV_RATE_LIMIT_PER_HOUR`
(default 20) case submissions per hour counted globally (`429` beyond);
benchmark seeds (`buyer_ref = __benchmark_seed__`) are exempt. The monthly
spend cap (`UAPV_SPEND_CAP_USD`, default 200) makes new cases
`spend_capped` rather than rejecting them. No other endpoint is rate
limited.

## 2. Infrastructure routes

### `GET /healthz`
Liveness. Auth: none. → `200 {"status":"ok"}` while the process is up.

### `GET /readyz`
Readiness. Auth: none, returns no case data. Performs: DB write probe
(`BEGIN IMMEDIATE; ROLLBACK` on a fresh connection), Sneferu SDK
reachability (mock mode counts as reachable), `ffmpeg`/`ffprobe` on PATH,
plus reported-only probes: astronomical archive status, ADS-B
configuration, orbital-catalog freshness (A-012 manual refresh), and
queued-case count. Per S8, `ready: true` requires exactly the DB write
probe, SDK reachability in live mode, and ffmpeg on PATH — archive,
ADS-B, and TLE reachability are reported but never gate readiness.

```bash
curl -s http://127.0.0.1:8470/readyz
```
```json
{"ready":true,"mode":"live","db":"ok","sdk":"reachable","ffmpeg":"ok",
 "archive":"unreachable","adsb":"unconfigured","tle":"unconfigured",
 "pending_cases":0}
```
Errors: `503 {"ready":false,"reason":"db not ok"|"sdk unreachable"|
"ffmpeg missing"}` — those three are the only readiness gates.

### `GET /metrics`

Prometheus text exposition with low-cardinality build, case-state, audit,
reference-integrity, and TLE status metrics. It emits no case
IDs, tokens, paths, or buyer references. The supplied public Caddy profile
blocks this route; scrape it only over a private monitoring path.

### `GET /`
Redirects `302 → /ui/login` (unauthenticated) or `303 → /ui/cases` when the
SPA is built; `302/303 → /login` or `/cases` when `ui/dist` is absent.

### SPA plumbing (only active as listed)
| Route | Behavior |
|---|---|
| `GET /ui` | `302 → /ui/cases` |
| `GET /ui/{path:path}` | Serves the built `ui/dist/index.html` shell with a server-injected `<script id="uapv-bootstrap">` JSON block (`{"authenticated", "csrf_token", "mode"}`), `Cache-Control: no-store`, and the CSP header (script/style/img/font/connect `'self'`-based; see `web_json.CSP_HEADER`). `503` text “UI not built — run 'npm run build' in ui/” when `ui/dist` is absent. |
| `GET /ui/assets/{path:path}` | Static mount of `ui/dist/assets` (registered at startup; if the build appears later, **restart the server**). Without a build: `503` text message. |
| `GET /ui/brand-mark.svg`, `GET /ui/app-icon.svg` | Production vector identity assets, `Cache-Control: public, max-age=86400`. |
| `GET /ui/brand-mark.png`, `GET /brand-mark.png` | Historical byte-exact B13 raster retained for provenance compatibility. |

## 3. Auth routes

### `GET /login`
HTML form (or JSON dialect of the page shell). Field: `token` (password).

### `POST /login`
Form field `token`. Success: session cookie set; HTML → `303 /cases`;
JSON dialect → `200 {"ok":true}`. Failure → `401` (“Invalid operator
token.”; JSON code `auth_failed`, never `auth_expired`).

```bash
curl -i -c cookies.txt -d "token=$UAPV_OPERATOR_TOKEN" http://127.0.0.1:8470/login
```

### `POST /logout`
Form field `csrf_token` (JSON dialect requires valid session + CSRF;
HTML path always succeeds and clears). Deletes the session, expires the
cookie (`Max-Age=0`), redirects `302 → /login` (JSON: `200 {"ok":true}`).

### `GET /terms`
Public. Full terms text (6 clauses) as HTML; JSON dialect →
`{"terms_text": "1. ... 2. ..."}`.

## 4. Intake

### `GET /cases/new`
Auth: session. HTML multipart form (or JSON intake-config payload for the
SPA). The page shows the static server-rendered cost estimate (FR-023):
`Estimated cost: up to $0.04 per case (test mock mode)` / `$0.20 (live mode)` —
`lineage_cost × expected_lineage_count (3) + fiction_cost`. This is an
upper bound because the verdict is unknown at intake, so the fiction cost
is always included.

Form fields: `media` (file, required), `observed_at` (ISO 8601 **with
offset**, required), `latitude`, `longitude` (decimal degrees, required),
`viewing_direction` (`azimuth` or `azimuth,elevation`), `shape`, `count`,
`duration_seconds`, `weather`, `behavior_notes` (≤ 4000 chars), `buyer_ref`,
`location_text`, `payment_status` (`unpaid|paid|comped`), `terms_accepted`
(checkbox, required), `csrf_token` (hidden).

### `POST /cases/new`
Auth: session + CSRF. `multipart/form-data`.
- Success (HTML): `303 → /cases/{case_id}`. Success (JSON dialect):
  `200 {"ok":true,"case_id","status":"queued|spend_capped","estimated_cost_usd"}`.
- Errors: `400` missing fields / terms not accepted (“terms acceptance
  required”), `413` oversized (images ≤ 50 MB / ≤ 20 MP; video ≤ 250 MB,
  ≤ 60 s), `415` unsupported or executable MIME (magic-byte sniffed), `422`
  undecodable media / implausible `observed_at` (> 1 y future, > 50 y past),
  `429` rate limited, `507` < 500 MB free disk. JSON dialect maps these to
  typed codes (`validation_error` carries per-field `errors[]`).

### `POST /api/v1/cases`
Auth: Bearer. `multipart/form-data` with file part `media`, the same fields
as above, and required boolean `terms_accepted` (`400 {"detail":"terms
acceptance required"}` without it). JSON bodies are not supported for media.
Success → `202 {"case_id","status","estimated_cost_usd"}`. Intake
validation errors return `422` (including per-field errors) except the
explicit terms `400`; `413/415/429/507` pass through with
`{"detail":"..."}`.

```bash
curl -s -X POST http://127.0.0.1:8470/api/v1/cases \
  -H "Authorization: Bearer $UAPV_OPERATOR_TOKEN" \
  -F media=@photo.jpg -F observed_at=2026-01-15T20:30:00+00:00 \
  -F latitude=34.05 -F longitude=-118.24 -F terms_accepted=true
```

No row is ever inserted when validation fails (intake runs before the
`INSERT`, `intake.create_case`).

## 5. Case list, detail, downloads

### `GET /cases`
Auth: session. Query: `status` (filter), `limit` (HTML default 50 max 200;
JSON default 50 max 200), `offset`. HTML: table of id/created/status/
verdict/buyer ref/payment + retry/rerun/delete row actions + retention badge
(older than `RETENTION_DAYS`, default 365). JSON:
`{"cases":[...],"total","retention_days","spend":{...},
"estimated_cost_usd","estimated_benchmark_cost_usd"}`.

### `GET /api/v1/cases`
Auth: Bearer. Query: `limit` (default 50, max 200), `offset`. → `200` JSON array of
`{case_id, created_at, status, verdict, buyer_ref, payment_status,
quality_score, agreement_fraction}`; total in `X-Total-Count` header.

```bash
curl -s -H "Authorization: Bearer $UAPV_OPERATOR_TOKEN" \
  http://127.0.0.1:8470/api/v1/cases?limit=50
```

### `GET /cases/{case_id}`
Auth: session. `404` unknown id. HTML: status badge, verdict + text,
download buttons, payment-update form, stage table (current `run_version`),
battery table, last 25 audit events; `<meta http-equiv="refresh" content="5">`
auto-refresh while `queued|analyzing|rerun_requested` (no JavaScript).
JSON: full detail payload — `case` (all columns + `report_available`,
`fiction_available`, `fiction_withheld`), `stages[]`, `battery_results[]`,
`lineage_outputs[]` (claims, status, confidence, provenance; capped 100 +
`total_lineage_outputs`), `analysis_contract` (runtime, sandbox, rigor,
coverage), `warnings[]`
(from `intake_warnings` audit events), `audit_events[]` (capped 100 +
`total_audit_events`), `downloads`. The removed phase-2 surfaces
(speculative xenoscience/story assets, buyer-token delivery) no longer
appear in the payload.

### `GET /cases/{case_id}/report`
Auth: session. Negotiated: HTML → `report.html` attachment; JSON →
the parsed `report.json` content. Eligible statuses: `verdict_ready`,
`complete`, and `rerun_requested` (newest archived
`report_{id}_v{N}.html`, falling back to the previous run's files before the
worker claims). Headers: `Content-Disposition: attachment`,
`X-UAPVF-SHA256` (stored hash). Errors: `404` unknown case, `409` report not
ready for this status, `500` eligible but file missing on disk.

### `GET /api/v1/cases/{case_id}/report.json`
Auth: Bearer. Serves `report.json` (≤ 500 KB) as `application/json`
attachment with `X-UAPVF-SHA256`. Same status rules as above.

### `GET /cases/{case_id}/fiction` / `GET /api/v1/cases/{case_id}/fiction`
Auth: session / Bearer. Serves the `.fic.md` seed as
`text/markdown; charset=utf-8` with `X-Content-Type-Options: nosniff` and an
attachment filename `fiction_{id}.fic.md`. `404 {"detail":"no fiction seed
for this case"}` when the verdict is `mundane_identified`, no seed exists
(including cleared-after-validation-failure), or the case is not
`verdict_ready|complete`. JSON dialect on the web route instead returns
`200 {"content","available","withheld","verdict"}` (only a missing case is
`404`).

## 6. Case actions (all CSRF-protected; JSON dialect returns `{ok}` or typed errors)

| Route | Purpose | Behavior |
|---|---|---|
| `POST /cases/{id}/retry` | Requeue a `failed` case | Same `run_version`, resumes after last completed stage. Errors: not `failed` → `conflict`; `retry_count ≥ 5` needs CLI `--force`. |
| `POST /cases/{id}/rerun` | Re-execute a `complete` case | Archives report, bumps `run_version`, audits `verdict_superseded`. Not `complete` → `conflict`. |
| `POST /cases/{id}/payment` | Set payment | Form field `payment_status` ∈ `unpaid|paid|comped`; audited (`payment_status_changed`, actor `operator`); `303 → /cases/{id}` (JSON `{ok:true}`); invalid value → `400`. |
| `GET /cases/{id}/delete/confirm` | Confirm page | `404` unknown; JSON → `{case_id,status,verdict,created_at,buyer_ref}`. |
| `POST /cases/{id}/delete` | Hard delete | Rows cascade, `var/cases/{id}/` removed; audit entries survive. JSON dialect refuses `analyzing` cases with `409 conflict`; HTML path follows the confirm page. |

## 7. Benchmark

### `GET /benchmark`
Auth: session. Latest `benchmark_runs` row (per-category recall,
false-no-mundane-match rate, insufficient-detection rate, pass/fail, prereg
hash) or “No benchmark run recorded.” During a run: “Processing seed case X
of Y…” via meta-refresh; failed runs render the stored error.

### `POST /benchmark/run`
Three paths:
- Bearer: runs synchronously off the event loop (`asyncio.to_thread`),
  returns the full result JSON. CLI `uapvf benchmark run` uses this path.
- JSON dialect (SPA): session + CSRF → `202` with initial progress object;
  run proceeds in a background thread, progress in `var/benchmark/progress.json`.
- Form: session + CSRF → `303 → /benchmark` immediately.
A concurrent second run is rejected `409` on all surfaces. Runs the 10 seed
cases in `var/benchmark/seed/` through the real pipeline against the
preregistration hash.

### `GET /api/v1/spend`
Auth: Bearer. → `200` monthly summary:
```json
{"month":"2026-08","month_total_usd":0.04,"all_time_usd":0.04,
 "entries_count":4,"spend_cap_usd":200.0,
 "payment_summary":{"unpaid":0,"paid":1,"comped":0},
 "estimated_paid_revenue_usd":50.0}
```

## 8. CLI reference (`uapvf`)

Exit codes: `0` success, `1` operational error, `2` usage error. Output is
JSON on stdout; errors go to stderr. Commands marked 🔒 require token
possession (`UAPV_CLI_TOKEN`, interactive prompt, or
`UAPV_ALLOW_LOCAL_ADMIN=1`).

| Command | What it does |
|---|---|
| `uapvf serve [--dev] [--host H] [--port P]` | Run server + 4-thread worker pool. Default `127.0.0.1:8470`, INFO logs. `--dev`: auto-reload, DEBUG, loopback-only (`--host` ignored). |
| `uapvf db init` | Idempotent schema creation (`var/uapvf.db`) + `var/{cases,benchmark,backup}/` + seeds missing config templates from the packaged defaults. No server, no token. |
| 🔒 `uapvf case submit --media PATH --fields FILE [--buyer-ref TEXT] [--payment-status unpaid\|paid\|comped]` | Full intake validation **before** any row exists; inserts `queued` (or `spend_capped`). Prints `{case_id, status, estimated_cost_usd}`. Exit `2` on unreadable fields file or missing `terms_accepted: true` in it; exit `1` on intake rejection. |
| 🔒 `uapvf case status <id>` | Full `cases` row + per-stage history (`pipeline_stage_runs`, ordered by `run_version`, `run_id`). |
| 🔒 `uapvf case export <id> [--out DIR]` | Copies `report.html`, `report.json`, `fiction_*.fic.md` (if present) after re-verifying each SHA-256 against the DB. Errors: `case is not complete` / `file integrity check failed: <name>` (exit 1). Audits `case_export`. |
| 🔒 `uapvf case fiction <id> [--out DIR]` | Copies the seed only. Exit 1 `no fiction seed for this case` when `mundane_identified`, no seed, or not `complete`; hash-verified. |
| 🔒 `uapvf case retry <id> [--force]` | `failed → queued`, `retry_count + 1`, resume from last completed stage. `--force` resets `retry_count` to 0. |
| 🔒 `uapvf case rerun <id>` | `complete → rerun_requested`; full re-execution on next claim with current data sources. |
| 🔒 `uapvf case delete <id>` | Hard delete (rows + files). Audit entries survive. |
| 🔒 `uapvf case set-payment <id> --status unpaid\|paid\|comped` | Updates + audits `payment_status_changed` with `{old, new}`. |
| 🔒 `uapvf benchmark prereg` | Canonicalizes + hashes `var/benchmark/prereg.json`; prints the thresholds + `prereg_hash`. |
| 🔒 `uapvf benchmark run` | **Requires the running server**: POSTs to `$UAPV_SERVER_URL` (default `http://127.0.0.1:8470`)/`benchmark/run` with Bearer; prints the result JSON. |
| 🔒 `uapvf benchmark results` | Latest `benchmark_runs` row + `calibration.json` (10 bins) if present. |
| 🔒 `uapvf audit <case_id>` | All audit events for one case. |
| 🔒 `uapvf audit verify <case_id>` / `uapvf audit verify --all` | Recomputes the hash chain; exit 1 on mismatch/break; reports `fork_points` where `prev_hash` does not match the preceding entry hash. A clean restore keeps the chain valid (its `restore_genesis` entry re-links to the old tail). |
| 🔒 `uapvf spend` | Monthly + all-time totals, entry count, payment summary, estimated paid revenue. |
| 🔒 `uapvf backup` | Consistent SQLite snapshot (online backup API) + `var/cases/` (excluding `analyzing` case dirs) + operator config + benchmark tree → `var/backup/uapvf_backup_<UTC ts>.tar.gz`. Audits `backup_created`. |
| 🔒 `uapvf restore PATH` | SIGTERMs the running server via `var/server.pid` (waits ≤ 30 s), path-traversal-guards the tarball, extracts over `var/`, reconciles `analyzing` cases (missing artifacts → `failed` “restored from backup with missing artifacts — re-submit or retry”), appends `restore_genesis` audit entry. |
| `uapvf version` | `{"name":"uapvf","version":"0.1.0"}`. |
| `uapvf --help`, `<subcommand> --help` | Usage; exit 0. |

### `fields.json` schema for `case submit`

```json
{
  "observed_at": "2026-01-15T20:30:00+00:00",
  "latitude": 34.05,
  "longitude": -118.24,
  "viewing_direction": "180,45",
  "shape": "light",
  "count": 1,
  "duration_seconds": 30,
  "weather": "clear",
  "behavior_notes": "Pulsating light moving west",
  "location_text": "Sedona, AZ",
  "terms_accepted": true
}
```

Required: `observed_at` (ISO 8601 with offset, within −50 y…+1 y),
`latitude` (−90…90), `longitude` (−180…180), `terms_accepted: true`.
Optional: everything else; `viewing_direction` is `azimuth` (0 ≤ az < 360)
or `azimuth,elevation` (−90 ≤ el ≤ 90); `behavior_notes` ≤ 4000 chars.
`lat=0,lon=0` is accepted with a `null_island` warning; a reported
`duration_seconds` differing > 50 % from ffprobe-measured duration adds a
`duration_mismatch` warning. Warnings appear in `report.json` `warnings`
and the report's evidence section — they never reject the case.

## 9. report.json sidecar schema (≤ 500 KB)

```json
{
  "case_id": "<uuid>",
  "verdict": "insufficient_data",
  "verdict_text": "<templated reasoning>",
  "uncertainty": {
    "value": null,
    "calibrated": false,
    "calibration_source": null,
    "low_confidence": false,
    "penalty": 0.0,
    "raw_confidence": 0.468333,
    "uncertainty_reason": "no calibration run available"
  },
  "battery_results": [ { "category": "aircraft", "result": "insufficient",
                         "evidence_citation": null, "source_stamp": {"source_id": "aircraft_unavailable", "reason": "...", "...": "..."} } ],
  "lineage_outputs": [ { "lineage_id": "mock-lineage-1", "classification": "unknown",
                         "artifact_detected": false, "hypothesis": "..." } ],
  "agreement_fraction": 1.0,
  "quality_score": 0.59,
  "quality_gate_pass": true,
  "warnings": [],
  "provenance": {"media_sha256": "...", "observed_at": "...", "latitude": 34.05, "longitude": -118.24},
  "generated_at": "<iso8601>"
}
```

When calibrated (live-mode benchmark calibration exists): `value` is the
binned probability, `calibrated: true`, `calibration_source` carries
`{validation_set_id, validation_set_hash}`, and `uncertainty_reason` cites
the validation set. `report.html` is capped at 1.5 MB — exceeding either cap
fails the case (“report size cap exceeded”), never truncates.
