# UAP Verdict Foundry — Operations Guide

For day-to-day case/spend/benchmark/audit/backup procedures see
[runbook.md](runbook.md); this document covers setup, configuration,
deployment, observability, and failure handling. Architecture:
[architecture.md](architecture.md).

## 1. Prerequisites

| Requirement | Detail | Verified |
|---|---|---|
| Python | ≥ 3.9 (`pyproject.toml`). The lockfile and packaging metadata are verified on 3.9.6; 3.11+ recommended but not required by code. | 3.9.6 |
| SQLite | ≥ 3.35 (bundled with CPython ≥ 3.9). WAL mode, `busy_timeout=5000`, FK enforcement on every connection. | stdlib `sqlite3` |
| ffmpeg / ffprobe | On PATH. Required for video intake, normalized buyer media, and story clips. Missing tools make `/readyz` fail. | `/opt/homebrew/bin` |
| OS sandbox | macOS `sandbox-exec` or Linux `bubblewrap`. Every decoder-facing intake and lineage operation fails closed without it. | platform facility |
| Disk | ≥ 500 MB free (enforced at intake with `507` below it). | — |
| Node 20+ + npm | Only to build the optional React console (`ui/`). The backend and all server-rendered screens work without it. | Node v26.5.0 |
| Runtime deps | `requirements.lock` (base): FastAPI, Uvicorn, pydantic/settings, multipart, Pillow, PyYAML, httpx, pytest. Live mode adds `.[live]`: filetype, Skyfield, SGP4, requests, and ONNX Runtime. Exact pins live in `requirements.lock`. | installed from lockfile |

## 2. Environment variables

All configuration is environment (+ optional `.env` in the working
directory, loaded by pydantic-settings). `.env.example` is the template.
Secrets are never logged.

### Required

| Name | Purpose | Notes |
|---|---|---|
| `UAPV_OPERATOR_TOKEN` | The single operator credential: web login, `/api/v1` bearer check, CLI possession check. | Any long random string. Set it in `.env`. No default — the shipped template value is a placeholder, not a shipped credential. |

### Mode

| Name | Purpose | Default |
|---|---|---|
| `SNEFERU_MOCK` | `1` = deterministic mock fixtures (fresh-install / development / CI default, FR-018); production must use `0`. | `1` |
| `UAPV_LINEAGE_RUNTIME` | `live` submits lineage analysis to the Sneferu engine through the adapter (FR-005); `mock` is test-only. Local lineage execution is deferred to phase 2. | `live` |
| `SNEFERU_SDK_URL` | Sneferu engine base URL (live mode only). | `http://127.0.0.1:7420` |
| `UAPV_REFERENCE_MODE` | DORMANT Phase 2 controlling-source tooling — informational only; it does not gate intake, processing, or readiness. `uapvf references status/fetch` report/verify the frozen sources. The strict intake gate was removed as unspec'd. | `strict` (inert) |

### External sources and signed model

| Name | Purpose |
|---|---|
| `ARCHIVE_MIRROR_PATH` | Local SQLite astronomical catalog mirror. A read-only fixture ships at `var/benchmark/catalog_fixture.sqlite` for benchmarks. |
| `ADSB_SOURCE_URL` | Production aircraft track feed. ADS-B Exchange requires `ADSB_SOURCE_API_KEY`; OpenSky requires `OPENSKY_CLIENT_ID` + `OPENSKY_CLIENT_SECRET` for live readiness. Anonymous OpenSky is evaluation-only. |
| `ADSB_SOURCE_API_KEY` | ADS-B Exchange `x-api-key`; secret, never logged. |
| `OPENSKY_CLIENT_ID`, `OPENSKY_CLIENT_SECRET` | OpenSky OAuth client credentials. |
| `TLE_CATALOG_URL` | Prefer CelesTrak current GP OMM JSON (`GROUP=ACTIVE&FORMAT=JSON`); refreshed MANUALLY via `uapvf tle refresh` (A-012) and never fetched per case. |
| `UAPV_EGRESS_ALLOWLIST` | Comma-separated host allowlist for controlled external fetches. |
| `UAPV_RESNET50_ONNX`, `UAPV_RESNET50_MAPPING`, `UAPV_RESNET50_RECEIPT`, `UAPV_RESNET50_TRUSTED_PUBLIC_KEY` | Exact artifacts and trust pin returned by `uapvf model install-resnet50`; used only by the deferred phase-2 local ResNet lineage tooling — not required by the MVP live path or `/readyz`. |

ADS-B, satellite, and astronomical sources are runtime adapters that report
`insufficient` when unavailable; they are not hard gates for `/readyz` or case
launch. A formal live-certification artifact is deferred to phase 2.

### Economics

| Name | Purpose | Default |
|---|---|---|
| `UAPV_COST_LINEAGE_USD` | Per-lineage estimated cost. | `0.01` mock / `0.05` live |
| `UAPV_COST_FICTION_USD` | Per-fiction-generation estimated cost (booked only when the engine text-generation call succeeds; the local fallback formatter spends nothing, FR-016). | `0.01` mock / `0.05` live |
| `UAPV_SPEND_CAP_USD` | Monthly compute cap; new cases park as `spend_capped`, in-flight cases always finish. | `200` |
| `RETENTION_DAYS` | Dashboard reminder-badge threshold only. No auto-deletion in the MVP — deletion is operator-initiated (spec §5). | `365` |

### Server / pipeline tuning

| Name | Purpose | Default |
|---|---|---|
| `UAPV_VAR_DIR` | State root (db, cases, benchmark, backup, config, `server.pid`). | `./var` |
| `UAPV_WORKER_THREADS` | Worker pool size; `0` disables processing (cases queue forever — tests use this). | `4` |
| `UAPV_WORKER_POLL_S` | Worker poll interval in seconds. | `0.2` |
| `UAPV_RATE_LIMIT_PER_HOUR` | Intake submissions/hour, counted globally; benchmark seeds exempt. | `20` |
| `UAPV_ALLOW_LOCAL_ADMIN` | `1` bypasses the CLI token check (loopback use; audit-warned). Leave unset in normal use. | unset |
| `UAPV_CLI_TOKEN` | CLI token (else interactive prompt). | unset |
| `UAPV_SERVER_URL` | Target server for `uapvf benchmark run`. | `http://127.0.0.1:8470` |

### Mock-mode test hooks (fixtures only — ignored when `SNEFERU_MOCK=0`)

`UAPV_MOCK_ZERO_LINEAGES=1` (zero lineages), `UAPV_MOCK_DRONE=1` (one
lineage classifies “drone”), `UAPV_MOCK_ARTIFACT=1` (all lineages detect a
lens artifact), `UAPV_MOCK_CLASSIFICATION=<x>` (override all lineage
classifications), `UAPV_MOCK_FICTION_INVALID=1` / `_FAIL=1` /
`_UNFIXABLE=1` (fiction validation ladder), `UAPV_MOCK_ADSB_POSITIVE=1`
(mock ADS-B match → `mundane_identified`). Two var-file hooks:
`var/mock_fail_next` (fail the next transient-retryable stage) and
`var/mock_pause_after_stage` (crash-simulate after a stage; cleared by the
worker). Every mock vision/fiction call appends to
`var/mock_call_log.jsonl` with a `call_id`.

## 3. Running locally

```bash
# install
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements.lock
python3 -m pip install -e .            # console script `uapvf`

# configure
cp .env.example .env                   # set UAPV_OPERATOR_TOKEN; for a local
                                       # demo set SNEFERU_MOCK=1 (the only
                                       # non-live mode — there is no
                                       # simulation runtime)

# initialize (idempotent; also seeds config templates from the wheel)
uapvf db init
# optional, dormant phase-2 tooling — NOT required to accept cases:
# uapvf references fetch

# start (default http://127.0.0.1:8470)
uapvf serve                            # or: uapvf serve --dev  (reload+DEBUG)

# tests
python3 -m pytest tests/ -q
```

**What live success looks like** (phase-2 live mode):

```bash
curl -s http://127.0.0.1:8470/healthz
{"status":"ok"}
curl -s http://127.0.0.1:8470/readyz
{"ready":true,"mode":"live","db":"ok","sdk":"reachable","ffmpeg":"ok",
 "archive":"unreachable","adsb":"unconfigured","tle":"unconfigured",
 "pending_cases":0}
```

Then submit a case (mock fixture) and watch it finish:

```bash
export UAPV_CLI_TOKEN="$UAPV_OPERATOR_TOKEN"
uapvf case submit --media tests/fixtures/unexplained.jpg --fields tests/fixtures/fields.json
# {"case_id": "...", "estimated_cost_usd": 0.08, "status": "queued"}
uapvf case status <case_id>
# status: complete, verdict: insufficient_data, all 12 stages completed
uapvf case export <case_id> --out /tmp/out
# report.html + report.json + fiction_<id>.fic.md, SHA-256 verified
```

The full pytest suite plus the SPA tests, lint/token audit, and production
build must pass before promotion. SPA-serving tests run when `ui/dist` is built
(`cd ui && npm install && npm run build`). UI tests (`cd ui && npm test`)
cover the React console.

## 4. Deployment

The supported production path is the single-node Linux profile in `deploy/`:
a hardened systemd unit, a Caddy TLS proxy, and a checked installation recipe.
The CI promotion gate is `.github/workflows/ci.yml`.

1. Build artifacts: build the console, run `scripts/sync-ui-dist.sh`, then
   `python3 -m pip install build && python3 -m build`
   → `dist/uapvf-0.1.0-py3-none-any.whl` + sdist (verified: the wheel
   contains source + `uapvf/defaults/` config templates + the tested console —
   no database, no `var/` state, no benchmark seed media).
2. On the host, install the wheel into `/opt/verdict-foundry/venv`, install
   FFmpeg and bubblewrap, and create `/etc/uapvf/uapvf.env` mode `0600` with
   the independent operator token secret.

3. Initialize the DB (`uapvf db init`). The dormant phase-2
   controlling-source tooling (`uapvf references fetch/status`) is optional
   and informational — it is not required to accept cases. Per S8, `/readyz`
   503s only for DB-write-probe
   failure, SDK-unreachable in live mode, or missing FFmpeg; archive, ADS-B,
   and TLE status are reported fields, never readiness gates. The full
   lineage-certification program (signed weights, operator collection,
   independence/calibration) is deferred phase-2 tooling.
4. Install `deploy/Caddyfile` with `UAPVF_HOSTNAME`; Caddy terminates TLS and
   keeps `/metrics` private. Keep exactly one service per `UAPV_VAR_DIR`.
5. Schedule backups at the required RPO and perform a restore drill. There
   is no automatic retention sweep; deletion is operator-initiated.

Live mode requires the Sneferu engine reachable at `SNEFERU_SDK_URL`
(assumption A-013 — verify the engine before accepting live cases; the
adapter fails loud rather than fabricating lineages). ADS-B and the
orbital catalog are optional per case: missing/stale coverage yields
`insufficient` categories, never invented negatives. Refresh the catalog
manually with `uapvf tle refresh` (A-012). Verify `/readyz` before
accepting live cases.

## 5. Observability

- **Logs**: uvicorn access lines + `uapvf.*` stdlib loggers on stdout
  (INFO in production, DEBUG with `--dev`). `nohup ... >> var/uapvf.log`
  is the §8 log path.
- **Health probes**: `GET /healthz` (alive), `GET /readyz` (DB write probe,
  SDK reachability, ffmpeg presence; archive/ADS-B/TLE/pending count
  reported; 503 with `"reason"` only for DB, live-mode SDK, or ffmpeg).
- **Metrics**: `GET /metrics` exposes low-cardinality Prometheus build, case
  state, audit-event, reference-integrity, and TLE status
  gauges. It contains no case IDs and is blocked by the public Caddy profile.
- **Audit trail**: `audit_events` hash chain — every status change, spend
  entry, payment change, export, retry/rerun/delete, backup/restore, and
  benchmark run, with `actor` (`system`/`operator`). Verify with
  `uapvf audit verify --all` (exit 1 on break; clean restore keeps the chain
  valid via its `restore_genesis` continuation entry).
- **Spend**: `GET /api/v1/spend` or `uapvf spend` — monthly/all-time
  totals, entry count, cap, payment summary, estimated paid revenue.
  Per-call rows in `spend_entries`.
- **Benchmark/calibration**: `benchmark_runs` rows (recall, false-no-mundane
  -match rate, insufficient-detection rate, pass/fail, prereg hash, mode)
  and `var/benchmark/calibration.json` (10 bins; applied to live cases
  only).
- **Mock call log**: `var/mock_call_log.jsonl` — one JSON line per mock
  engine call (mock mode).

## 6. Troubleshooting

### 6.1 `address already in use` when starting `uapvf serve`
Symptom: uvicorn logs `[Errno 48] error while attempting to bind on address
('127.0.0.1', 8470)` and exits. Diagnosis: `lsof -i :8470 -sTCP:LISTEN` —
identify the holding process (a stray server from an earlier session is the
common cause). Fix: stop that process or start on another port:
`uapvf serve --port 8491` (then use `--port`/`UAPV_SERVER_URL` consistently
for benchmark runs). Never run two servers against the same `UAPV_VAR_DIR`.

### 6.2 `/ui/*` answers “UI not built — run 'npm run build' in ui/”
even though `ui/dist` now exists. Cause: the `/ui/assets` static mount and
brand-mark routes register **at server startup**; a build that appears later
is invisible to them (the shell route itself reads `index.html` per request,
so the page HTML may load while assets 503). Fix: build
(`cd ui && npm install && npm run build`) and **restart** `uapvf serve`.

### 6.3 Login returns 401 with “the right” token
Causes, in order of likelihood: (1) you are hitting a *different* server
instance than the one reading your `.env` — check `lsof -i :8470` for stray
processes and verify `curl -s localhost:8470/readyz` shows the `var` dir you
expect; (2) the server was started before you edited `.env` — it reads the
environment at process start, so restart it; (3) token copy error (trailing
whitespace/newline from the editor). Diagnosis: `curl -i -H "Accept:
application/json" -d "token=***" localhost:8470/login` → `auth_failed`
means token mismatch at the process you actually reached.

### 6.4 Cases stay `queued` forever
The worker pool runs **inside `uapvf serve`** — `uapvf case submit` (CLI)
only validates and queues. Diagnosis: is the server process alive
(`curl localhost:8470/healthz`)? Is `UAPV_WORKER_THREADS=0` in its
environment (tests set this)? Is the case `spend_capped` instead (see 6.6)?
Fix: start/repair the server; unset `UAPV_WORKER_THREADS=0`.

### 6.5 Case `failed` with an error in the audit trail
Read the reason: `uapvf case status <id>` (`error_detail`) or the case's
audit events. Taxonomy: transient failures already retried 3× (1/2/4 s
backoff) before failing — `uapvf case retry <id>` (≤ 5 retries, `--force`
resets). Unrecoverable classes never auto-retry: battery configuration
errors (fix `var/battery_config.yaml`, then retry), export lint violation,
report size cap exceeded, schema mismatch. Unreachable external sources do
**not** fail cases — they produce `insufficient` category results.

### 6.6 Case `spend_capped`
The monthly cap (`UAPV_SPEND_CAP_USD`) is reached. The case holds; it is
not lost. Fix: raise the cap in `.env` and restart the server, or wait for
the UTC month rollover — the worker pool releases capped cases automatically
when budget allows. In-flight cases are never interrupted by the cap.

### 6.7 `/readyz` reports `503`
Per S8, `"reason"` names exactly one of three failing checks: `db not ok`
(database file locked/missing — check `UAPV_VAR_DIR` and that only one
server uses it), `ffmpeg missing` (install ffmpeg; video intake then 507s
until fixed), or live-mode `sdk unreachable` (engine down at
`SNEFERU_SDK_URL`). Archive, ADS-B, and TLE status are reported fields in
the same response and never gate readiness.

### 6.8 `database is locked`
Every connection sets WAL + 5 s busy timeout, so brief contention resolves
itself. Persistent locks mean two processes fighting over the same db (two
servers on one `UAPV_VAR_DIR`, or a long external transaction). Fix: keep
exactly one server per state dir; `uapvf backup` uses SQLite's online backup
API and never blocks the server.

## 7. Backup, restore, upgrade

- `uapvf backup` → `var/backup/uapvf_backup_<UTC ts>.tar.gz`: consistent
  SQLite snapshot + case files (excluding in-flight `analyzing` dirs) +
  operator config + benchmark tree.
- `uapvf restore <tarball>` stops the running server via `var/server.pid`,
  extracts over `var/`, reconciles `analyzing` rows (missing artifacts →
  `failed` “re-submit or retry”), and appends a `restore_genesis` audit
  entry so the hash chain stays verifiable end to end.
- Version upgrade: stop the server (restore path or signal), install the new
  version (`pip install -e .` or the new wheel), `uapvf db init`
  (idempotent), start. There is no migration framework in MVP —
  backup/restore is the manual migration path (spec §5).
