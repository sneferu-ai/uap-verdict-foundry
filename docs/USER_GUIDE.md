# UAP Verdict Foundry — Product Guide

This guide is for the person **using** the product: submitting skywatcher
media, reading verdicts, exporting reports, and keeping the service running
on your machine. For how the system is built see
[architecture.md](architecture.md); for every route and command see
[API.md](API.md); for keeping it running see
[OPERATIONS.md](OPERATIONS.md) and [runbook.md](runbook.md).

## What you get

For each photo or video you submit with a capture time and place, the
service produces:

- A **tri-state verdict** — `mundane_identified` (a tested everyday
  explanation matched), `insufficient_data` (at least one category could not
  be tested on your inputs), or `no_mundane_match` (everything tested,
  nothing matched).
- A **report** (`report.html` + `report.json`) with templated reasoning,
  per-category evidence citations with provenance stamps, and calibrated
  uncertainty. Verdict text is templated and never asserts extraterrestrial
  origin — the product cannot make that claim, by design.
- For unresolved cases only: a **fiction seed** — a short speculative
  narrative for your content pipeline, permanently labelled
  `[SPECULATIVE FICTION — NOT FORENSIC EVIDENCE]` on every paragraph. Treat
  it as story material, never as analysis.
- A **case list, audit trail, spend ledger, and calibration benchmark** so
  you can defend how each verdict was reached.

## 1. Onboarding — start here

The service runs on your machine at **http://127.0.0.1:8470**.

1. Install and start it once (full details in
   [OPERATIONS.md](OPERATIONS.md)):

   ```bash
   python3 -m venv .venv && source .venv/bin/activate
   python3 -m pip install -r requirements.lock && python3 -m pip install -e .
   cp .env.example .env        # then set UAPV_OPERATOR_TOKEN to a long random string
   uapvf db init
   uapvf references fetch
   uapvf serve
   ```

2. Open **http://127.0.0.1:8470/** in your browser. You land on the login
   screen (the console at `/ui/login` when the UI bundle is built, the plain
   `/login` form otherwise — both work identically).

Fresh installs start in **mock/degraded mode** so the smallest deploy works
without live credentials or fetched references. The mode model is exactly two
modes: the live Sneferu engine (FR-005) or deterministic mock fixtures
(`SNEFERU_MOCK=1`, FR-018). There is no simulation runtime — the removed
third mode silently routed to the paid live engine while labelling records
"simulation", so selecting it now fails closed with a named error. Use
`SNEFERU_MOCK=1` for a visibly labelled local demonstration; switch to
`SNEFERU_MOCK=0` and `UAPV_REFERENCE_MODE=strict` (after `uapvf references
fetch`) for production claims.

## 2. Signing in

- Your credential is the **operator token** — the `UAPV_OPERATOR_TOKEN`
  value you set in `.env`. This is the intentional development credential:
  one token for the single operator of this local service. There is no
  username, no user table, no password recovery.
- Enter the token on the login screen. Success takes you to the case
  dashboard. The session lasts **12 hours**; after that, log in again with
  the same token.
- Wrong token → “Invalid operator token.” and you stay on the login screen.

## 3. Primary journey — submit a capture, get a verdict, export the report

1. **Open the dashboard** (`/ui/cases` or `/cases`).
2. Click **New case**. You see the intake form with the static cost estimate
   (three lineage analyses and a narrative seed) — an upper bound because the
   verdict is unknown at intake.
3. **Attach the media** (one photo or one video):
   - images up to 50 MB / 20 megapixels (JPEG, PNG, WEBP, HEIC…);
   - videos up to 250 MB / 60 seconds (MP4, MOV…) — needs `ffmpeg` installed.
4. **Fill the capture fields.** Required: `observed_at` (ISO 8601 **with
   offset**, e.g. `2026-01-15T20:30:00+00:00`), `latitude`, `longitude`.
   Optional but recommended: `viewing_direction` (`azimuth` or
   `azimuth,elevation` — several categories can only be tested with it),
   `shape`, `count`, `duration_seconds`, `weather`, `behavior_notes`,
   `location_text`.
5. **Set the business fields**: `buyer_ref` (your reference for the paying
   client), `payment_status` (`unpaid` / `paid` / `comped`).
6. **Accept the terms** checkbox and submit. The case is created as
   `queued`; you land on its detail page. (Submission is rate-limited to 20
   cases/hour; exceeding the monthly spend cap parks the case as
   `spend_capped` instead of rejecting it.)
7. **Watch it run.** The detail page polls through ten stages: intake →
   quality gate → lineage analysis → rigor adjudication → mundane battery →
   coverage check → verdict → uncertainty → report → text-only fiction seed
   (unresolved cases only, FR-004). The contract card shows
   runtime, OS-sandbox proof, rigor, and coverage separately.
8. **Read the verdict.**
   - `mundane_identified`: look at the battery table — the matching category
     shows its evidence citation (e.g. a flight track matched within ±5 min
     / ±2 km) and provenance stamp.
   - `insufficient_data`: the stage rows tell you *which* category lacked
     coverage (e.g. no ADS-B source reachable) and why — this is a coverage
     statement, not an exoneration statement.
   - `no_mundane_match`: every configured category was tested and none
     matched.
   - If vision lineages disagreed (agreement fraction < 0.6), the report
     flags low confidence; uncertainty is `uncalibrated (null)` until a
     **live-mode** benchmark calibration exists (see step 10 and the
     runbook).
9. **Download.** Report: `report.html` (plus the `report.json` sidecar for
   machine use). For unresolved cases the fiction seed button gives you the
   labelled `.fic.md`. Mundane cases have no fiction — that is correct
   behavior, not a bug.
10. **Export and deliver.** The MVP has no buyer-facing delivery surface —
    you export the report and deliver it manually (forum post, email, etc.):

    ```bash
    uapvf case export <case_id> --out ~/exports/
    ```

    copies the report, JSON sidecar, and fiction seed into a folder after
    re-verifying each file's SHA-256. Deliver through your forum/email
    workflow; the report is self-contained. (Token-gated buyer delivery is
    deferred to phase 2.)
11. **Record payment** when the client pays: on the case page choose `paid`
    (or use `uapvf case set-payment <id> --status paid`). The spend ledger
    tracks estimated compute cost and paid revenue so you can see the margin
    per case.

The same journey works entirely from the terminal if you prefer:
`uapvf case submit --media photo.jpg --fields fields.json` →
`uapvf case status <id>` → `uapvf case export <id> --out DIR`
(see [examples/](../examples/README.md) for copy-paste scripts).

## 4. In-product help

- **Cost estimate** is always shown on the intake form (static, upper bound).
- **Terms** are linked from the login and intake screens (`/terms`), and the
  short terms summary appears on the intake form itself.
- **System status**: the console's status popover shows mode (mock/live) and
  readiness; the same facts are at `/readyz`.
- **Stage and evidence detail** on each case page: stage history, battery
  evidence citations, source stamps, warnings (e.g. `null_island`,
  `duration_mismatch`), and the full audit trail.
- **This guide** + [runbook.md](runbook.md) for daily operations.

## 5. Restart and recovery

- **Stop**: Ctrl-C the `uapvf serve` process (or follow the runbook's
  restore-based stop for background servers).
- **Start again**: `uapvf serve`. The case list, reports, audit trail, spend
  ledger, and benchmark history all persist — everything lives in
  `var/` (database + files), never in memory.
- **Sign back in** with the same token from `.env`.
- **In-flight work survives**: a case that was `analyzing` at shutdown is
  reset to `queued` at startup and resumes from its last completed stage —
  you do not re-pay for finished stages (a recovered duplicate external call
  is audited as `duplicate_call_recovery`).
- **Verify your saved work** after a restart: the dashboard shows your
  cases; `uapvf audit verify --all` recomputes the audit hash chain and
  reports any break; `uapvf case export` refuses to copy a file whose hash
  no longer matches the database.
- **Backups**: `uapvf backup` writes a timestamped tarball to `var/backup/`
  (database snapshot + case files + config + benchmark tree). Restore with
  `uapvf restore var/backup/<file>.tar.gz` — see the runbook for the exact
  semantics.

## 6. Common errors and what to do

| You see | Why | Do this |
|---|---|---|
| “Invalid operator token.” at login | Token doesn't match `.env` | Open `.env`, copy the exact `UAPV_OPERATOR_TOKEN` value; if you changed `.env`, restart `uapvf serve` (it reads the environment at startup). |
| Session expired mid-work | Sessions live 12 h | Log in again; nothing was lost. |
| “CSRF validation failed” (403) | Stale form after a long idle tab | Reload the page and resubmit — the new CSRF token ships with the page. |
| `413` at submit | Media too big (> 50 MB/20 MP image, > 250 MB/60 s video) | Trim or downscale the media; the caps are product policy. |
| `415` at submit | Unsupported or non-media file type (magic-byte checked — renaming an executable doesn't help) | Submit an actual image/video. |
| `422` at submit | Undecodable media or implausible `observed_at` (more than 1 year in the future / 50 years in the past) | Fix the timestamp format (ISO 8601 **with offset**) or the file. |
| `429` at submit | More than 20 submissions in an hour | Wait out the hour; the limit is global. |
| Case stuck in `queued` | The worker pool lives inside `uapvf serve` — CLI submission only queues | Make sure `uapvf serve` is running; cases process within a second of a free worker. |
| Case `failed` | Transient failure after 3 retries, or an unrecoverable one (battery misconfiguration, report size cap, export lint violation) — reason in the audit trail / `error_detail` | For config: fix `var/battery_config.yaml`, then `uapvf case retry <id>`. Up to 5 retries; `--force` resets the counter. |
| Case `spend_capped` | Monthly compute cap (`UAPV_SPEND_CAP_USD`, default $200) reached | Raise the cap in `.env` + restart, or wait for the UTC month rollover — capped cases re-queue automatically when budget allows. |
| Report download says `409` / “not yet available” | Case hasn't reached `verdict_ready` (queued/analyzing/failed) | Wait for completion; after a `rerun` the archived report stays downloadable. |
| Fiction button missing or `404` | Verdict was `mundane_identified` (no fiction by design), or the seed was cleared after label validation failed | Nothing to do — a resolved case correctly has no fiction. |
| `/ui/*` says “UI not built” | The React bundle wasn't compiled when the server started | `cd ui && npm run build`, then **restart** `uapvf serve`; the plain `/cases` screens work meanwhile. |
| `error: address already in use` starting the server | Port 8470 is taken | Check with `lsof -i :8470`; stop the other process or run `uapvf serve --port <free-port>`. |
| `ffmpeg not found` in `/readyz` | ffmpeg/ffprobe not installed | Install ffmpeg. The process may serve existing image reports, but it is not production-ready. |

## 7. Deletion and retention

Deleting a case (`/cases/{id}/delete/confirm` → confirm, or `uapvf case
delete <id>`) is a verified hard purge: database rows, case files, buyer
capabilities, and recoverable copies inside product backups are removed. A
disposable restore check proves absence; the audit trail retains only the fact
that deletion happened. An active legal hold blocks deletion.

There is NO automatic deletion in the MVP (spec §5 retention): case data is
retained until you delete it. Cases older than `RETENTION_DAYS` (default
365) get an informational dashboard reminder badge — that badge is the whole
retention mechanism in this slice. Automatic TTL enforcement is deferred to
phase 2.
