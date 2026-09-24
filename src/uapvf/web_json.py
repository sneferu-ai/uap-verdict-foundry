"""JSON response layer for the SPA operator console.

Implements the UI spec's content-negotiation contract (§3.3–§3.9, §7):
RFC 7231 Accept matching, typed ``code`` fields on JSON errors, and the
payload builders consumed by the React console under ``/ui/*``.

The HTML scaffold stays byte-identical: handlers consult :func:`wants_json`
and only take a JSON branch when the Accept header selects it (BE-03).
Nothing in this module mutates pipeline, verdict, or audit semantics.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from fastapi.responses import JSONResponse

from uapvf.config import (
    EXPECTED_LINEAGE_COUNT,
    LineageRuntimeRemovedError,
    Settings,
)


# ---------------------------------------------------------------------------
# Content negotiation (spec §3.3, D-32)
# ---------------------------------------------------------------------------

def _parse_accept(header: Optional[str]) -> list:
    """Parse an Accept header into [(media_type, q, specificity), ...]."""
    if not header:
        return []
    entries = []
    for part in header.split(","):
        part = part.strip()
        if not part:
            continue
        segments = part.split(";")
        media = segments[0].strip().lower()
        if not media:
            continue
        q = 1.0
        for param in segments[1:]:
            param = param.strip()
            if param.startswith("q="):
                try:
                    q = max(0.0, min(1.0, float(param[2:])))
                except ValueError:
                    q = 1.0
        specificity = media.count("*")  # 0 = exact, 1 = subtype, 2 = */*
        entries.append((media, q, specificity))
    return entries


def _best_q(entries: list, exact: str, prefix: str) -> tuple:
    """Best (q, specificity) for a target type; more specific wins ties."""
    best = (0.0, -1)
    for media, q, spec in entries:
        if media == exact:
            match_spec = 2
        elif media == prefix or media == f"{prefix.split('/')[0]}/*":
            match_spec = 1
        elif media == "*/*":
            match_spec = 0
        else:
            continue
        if (match_spec > best[1]) or (match_spec == best[1] and q > best[0]):
            best = (q, match_spec)
    return best


def wants_json(accept_header: Optional[str]) -> bool:
    """True when application/json outranks text/html per spec §3.3.

    - ``Accept: application/json`` → JSON
    - ``Accept: text/html, application/json;q=0.8`` → HTML
    - ``Accept: */*`` or absent → HTML (handler default, D-32)
    - ``Accept: application/json;q=0`` → HTML (explicit rejection)
    """
    entries = _parse_accept(accept_header)
    if not entries:
        return False
    q_json, _ = _best_q(entries, "application/json", "application/*")
    q_html, _ = _best_q(entries, "text/html", "text/*")
    return q_json > 0 and q_json > q_html


def wants_json_request(request) -> bool:
    return wants_json(request.headers.get("accept"))


# ---------------------------------------------------------------------------
# Typed error responses (spec §3.4 table; D-20)
# ---------------------------------------------------------------------------

def jerr(message: str, code: str, status: int, ok: bool = False,
         extra: Optional[dict] = None) -> JSONResponse:
    """Error responses are never ok (§3.4 — uniform {error, code, ok})."""
    body = {"error": message, "code": code, "ok": ok}
    if extra:
        body.update(extra)
    return JSONResponse(body, status_code=status)


# ---------------------------------------------------------------------------
# Shared computations
# ---------------------------------------------------------------------------

def _current_month() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m")


def _retention_cutoff_iso(settings: Settings) -> str:
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=int(settings.RETENTION_DAYS))
    return cutoff.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


def _retention_exceeded(created_at: Optional[str], settings: Settings) -> bool:
    if not created_at:
        return False
    return created_at < _retention_cutoff_iso(settings)


def estimated_benchmark_cost_usd(settings: Settings) -> Optional[float]:
    """Upper bound for a benchmark run: seeds × per-case estimate.

    Returns None when the seed manifest is absent/invalid (honest null —
    the UI then shows the 'cost estimate unavailable' notice, D-41).
    """
    try:
        from uapvf import benchmark as benchmod

        manifest = benchmod.load_seed_manifest(settings)
        return round(len(manifest) * settings.estimated_case_cost_usd(), 6)
    except Exception:
        return None


def _lineage_runtime_for_read(settings: Settings) -> str:
    """Read-side lineage-runtime fallback (round-5 hardening).

    Fail-closed raising on a removed runtime belongs on the SPEND paths
    (sneferu_adapter._fail_closed_runtime_guard). A case-detail READ must
    never 500 because the operator left a stale UAPV_LINEAGE_RUNTIME in
    the environment, so degrade to the strictest valid mode instead.

    Only the known stale-knob exception is swallowed; other failures are
    allowed to surface so the operator can fix them.
    """
    try:
        return settings.lineage_runtime
    except LineageRuntimeRemovedError:
        return "live"


def spend_payload(conn, settings: Settings) -> dict:
    """Spec §7.3 SpendSummary, computed from spend_entries for this month."""
    from uapvf import spend as spendmod

    month = _current_month()
    month_total = spendmod.month_total_usd(conn, month)
    cap = float(settings.UAPV_SPEND_CAP_USD)
    like_month = "substr(recorded_at, 1, 7) = ?"
    lineage_calls = conn.execute(
        f"SELECT COUNT(*) AS n FROM spend_entries WHERE {like_month} "
        "AND description LIKE '%lineage:%'", (month,)).fetchone()["n"]
    fiction_calls = conn.execute(
        f"SELECT COUNT(*) AS n FROM spend_entries WHERE {like_month} "
        "AND description LIKE '%fiction:%'", (month,)).fetchone()["n"]
    lineage_cost = conn.execute(
        f"SELECT COALESCE(SUM(cost_usd), 0.0) AS t FROM spend_entries "
        f"WHERE {like_month} AND description LIKE '%lineage:%'",
        (month,)).fetchone()["t"]
    fiction_cost = conn.execute(
        f"SELECT COALESCE(SUM(cost_usd), 0.0) AS t FROM spend_entries "
        f"WHERE {like_month} AND description LIKE '%fiction:%'",
        (month,)).fetchone()["t"]
    # Round 4: the spend summary names exactly the two SDK call classes the
    # MVP makes (FR-016: lineage + fiction). The removed xenoscience judge
    # stage never spends, so it has no summary line.
    return {
        "month": month,
        "total_usd": round(float(month_total), 6),
        "spend_cap_usd": cap,
        "remaining_usd": round(max(0.0, cap - float(month_total)), 6),
        "lineage_calls": int(lineage_calls),
        "fiction_calls": int(fiction_calls),
        "lineage_cost_usd": round(float(lineage_cost), 6),
        "fiction_cost_usd": round(float(fiction_cost), 6),
    }


# ---------------------------------------------------------------------------
# Case payloads
# ---------------------------------------------------------------------------

def case_summary(row, settings: Settings) -> dict:
    return {
        "case_id": row["case_id"],
        "created_at": row["created_at"],
        "status": row["status"],
        "verdict": row["verdict"],
        "buyer_ref": row["buyer_ref"],
        "payment_status": row["payment_status"],
        "quality_score": row["quality_score"],
        "agreement_fraction": row["agreement_fraction"],
        "retention_exceeded": _retention_exceeded(row["created_at"], settings),
    }


def case_list_payload(conn, settings: Settings, status: Optional[str],
                      limit: int, offset: int) -> dict:
    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    where = ""
    params: list = []
    if status:
        where = " WHERE status = ?"
        params.append(status)
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM cases{where}", params).fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM cases{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset]).fetchall()
    return {
        "cases": [case_summary(r, settings) for r in rows],
        "total": int(total),
        "retention_days": int(settings.RETENTION_DAYS),
        "spend": spend_payload(conn, settings),
        "estimated_cost_usd": settings.estimated_case_cost_usd(),
        "estimated_benchmark_cost_usd": estimated_benchmark_cost_usd(settings),
    }


def _report_on_disk(row, case_dir_fallback: Path) -> bool:
    if row["status"] in ("verdict_ready", "complete"):
        return bool(row["report_path"]) and Path(row["report_path"]).exists()
    if row["status"] == "rerun_requested":
        # FR-022: the archived copy remains downloadable during a rerun.
        case_dir = (Path(row["report_path"]).parent if row["report_path"]
                    else case_dir_fallback)
        if any(case_dir.glob(f"report_{row['case_id']}_v*.html")):
            return True
        return bool(row["report_path"]) and Path(row["report_path"]).exists()
    return False


def _fiction_on_disk(row) -> bool:
    return bool(
        row["fiction_ready"]
        and row["fiction_path"]
        and Path(row["fiction_path"]).exists()
        and row["status"] in ("verdict_ready", "complete")
    )


# Verdicts for which the fiction stage actually attempts a seed. A
# mundane_identified case never gets one by construction
# (pipeline.py::_stage_fiction) and has its own explicit gate, so it is
# "no seed by design", never "withheld".
_FICTION_ELIGIBLE_VERDICTS = ("no_mundane_match", "insufficient_data")


def _fiction_withheld(row) -> bool:
    """Derive the withheld terminal state without a schema column.

    The fiction stage has a designed terminal where it COMPLETES but its
    output is cleared after label-validation failure
    (pipeline.py::_stage_fiction): fiction_ready=1, fiction_path=NULL,
    status='complete', audit 'cleared_after_validation_failure'. The
    stage ran and finished; its seed will never arrive. Reporting that
    case as "not ready yet" is a false terminal state — the honesty
    failure SOUL.md ¶1 names. The status='complete' guard keeps the
    mid-rerun window truthful (a queued rerun is not terminal again yet,
    so the UI reads "not ready" for exactly as long as that is true).
    """
    return bool(
        row["fiction_ready"]
        and row["fiction_path"] is None
        and row["status"] == "complete"
        and row["verdict"] in _FICTION_ELIGIBLE_VERDICTS
    )


def case_detail_payload(conn, settings: Settings, row) -> dict:
    case_id = row["case_id"]
    case_dir = settings.cases_dir / case_id

    stages = conn.execute(
        "SELECT * FROM pipeline_stage_runs WHERE case_id = ? AND "
        "run_version = ? ORDER BY run_id",
        (case_id, row["run_version"])).fetchall()
    battery = conn.execute(
        "SELECT * FROM battery_results WHERE case_id = ? AND run_version = ? "
        "ORDER BY category",
        (case_id, row["run_version"])).fetchall()
    lineage_total = conn.execute(
        "SELECT COUNT(*) AS n FROM lineage_outputs WHERE case_id = ? AND "
        "run_version = ?", (case_id, row["run_version"])).fetchone()["n"]
    lineage = conn.execute(
        "SELECT * FROM lineage_outputs WHERE case_id = ? AND run_version = ? "
        "ORDER BY lineage_id LIMIT 100",
        (case_id, row["run_version"])).fetchall()
    audit_total = conn.execute(
        "SELECT COUNT(*) AS n FROM audit_events WHERE case_id = ?",
        (case_id,)).fetchone()["n"]
    audit_rows = conn.execute(
        "SELECT * FROM audit_events WHERE case_id = ? "
        "ORDER BY recorded_at DESC, event_id DESC LIMIT 100",
        (case_id,)).fetchall()
    rigor_row = conn.execute(
        "SELECT * FROM rigor_runs WHERE case_id=? AND run_version=?",
        (case_id, row["run_version"])).fetchone()
    coverage_row = conn.execute(
        "SELECT * FROM case_coverage WHERE case_id=? AND run_version=?",
        (case_id, row["run_version"])).fetchone()
    # Round 4: xenoscience, story assets, and buyer-token delivery were
    # deferred to phase 2 and their surfaces removed — the detail payload
    # no longer queries or projects them (dead controls shipped as broken
    # UI were the round-3 reopen).

    def _parsed(value, fallback):
        try:
            return json.loads(value) if value else fallback
        except Exception:
            return fallback

    # Warnings are persisted as intake_warnings audit events (FR-024).
    warnings: list = []
    for a in audit_rows:
        if a["action"] == "intake_warnings" and a["detail_json"]:
            try:
                detail = json.loads(a["detail_json"])
                for w in detail.get("warnings", []):
                    warnings.append({
                        "code": w.get("type", "warning"),
                        "message": w.get("message", ""),
                    })
            except Exception:
                continue
    warnings = warnings[:100]

    case = {
        **case_summary(row, settings),
        "observed_at": row["observed_at"],
        "latitude": row["latitude"],
        "longitude": row["longitude"],
        "location_text": row["location_text"],
        "verdict_text": row["verdict_text"],
        "uncertainty": row["uncertainty"],
        "uncertainty_calibrated": row["uncertainty_calibrated"],
        "uncertainty_reason": row["uncertainty_reason"],
        "calibration_source": row["calibration_source"],
        "quality_gate_pass": row["quality_gate_pass"],
        "lineage_count": row["lineage_count"],
        "retry_count": row["retry_count"],
        "run_version": row["run_version"],
        "current_stage": row["current_stage"],
        "error_detail": row["error_detail"],
        "report_available": _report_on_disk(row, case_dir),
        "fiction_available": _fiction_on_disk(row),
        # The schema carries no withhold column, but the state is
        # derivable: a completed fiction stage whose output was cleared
        # after label-validation failure is withheld, not "not ready".
        "fiction_withheld": _fiction_withheld(row),
        "estimated_cost_usd": settings.estimated_case_cost_usd(),
    }
    return {
        "case": case,
        "stages": [
            {
                "stage_name": s["stage"],
                "run_version": s["run_version"],
                "status": s["status"],
                "started_at": s["started_at"],
                "completed_at": s["completed_at"],
                "error_detail": s["error_detail"],
            }
            for s in stages
        ],
        "battery_results": [
            {
                "category": b["category"],
                "result": b["result"],
                "evidence_citation": b["evidence_citation"],
                "source_stamp_json": b["source_stamp_json"],
                "recorded_at": b["recorded_at"],
            }
            for b in battery
        ],
        "lineage_outputs": [
            {
                "lineage_id": l["lineage_id"],
                "classification": l["classification"],
                "artifact_detected": l["artifact_detected"],
                "hypothesis": l["hypothesis"],
                "status": l["status"] or "unknown",
                "confidence": l["confidence"],
                "evidence_claims": _parsed(l["evidence_claims_json"], []),
                "provenance": _parsed(l["provenance_json"], {}),
                "abstention_reason": l["abstention_reason"],
            }
            for l in lineage
        ],
        "analysis_contract": {
            "lineage_runtime": row["lineage_runtime"]
            or _lineage_runtime_for_read(settings),
            "expected_lineages": EXPECTED_LINEAGE_COUNT,
            "sandbox_verified": bool(row["sandbox_verified"]),
            "coverage": ({
                "expected_categories": _parsed(
                    coverage_row["expected_categories_json"], []),
                "tested_categories": _parsed(
                    coverage_row["tested_categories_json"], []),
                "insufficient_categories": _parsed(
                    coverage_row["insufficient_categories_json"], []),
                "complete": bool(coverage_row["complete"]),
            } if coverage_row else None),
            "rigor": ({
                "runtime": rigor_row["runtime"],
                "agreement_fraction": rigor_row["agreement_fraction"],
                "population_variance": rigor_row["population_variance"],
                "plurality": rigor_row["plurality"],
                "plurality_count": rigor_row["plurality_count"],
                "conditions": _parsed(rigor_row["conditions_json"], {}),
                "refutation": _parsed(rigor_row["refutation_json"], {}),
                "passed": bool(rigor_row["passed"]),
                "reason": rigor_row["reason"],
            } if rigor_row else None),
        },
        "warnings": warnings,
        "audit_events": [
            {
                "entry_hash": a["entry_hash"],
                "case_id": a["case_id"],
                "actor": a["actor"],
                "action": a["action"],
                "detail": a["detail_json"] or "",
                "recorded_at": a["recorded_at"],
            }
            for a in audit_rows
        ],
        "total_lineage_outputs": int(lineage_total),
        "total_warnings": len(warnings),
        "total_audit_events": int(audit_total),
        "downloads": {
            "report_available": case["report_available"],
            "fiction_available": case["fiction_available"],
            "fiction_withheld": case["fiction_withheld"],
        },
    }


def intake_config_payload(settings: Settings) -> dict:
    # Derived from the intake constants so the SPA payload can never
    # drift from the enforced caps (round-4 blocker: it advertised 120 s
    # while FR-001 mandates ≤ 60 s).
    from uapvf.intake import (
        IMAGE_MAX_BYTES,
        IMAGE_MAX_MP,
        VIDEO_MAX_BYTES,
        VIDEO_MAX_DURATION_S,
    )

    estimate = settings.estimated_case_cost_usd()
    mode_label = "mock mode" if settings.mock_mode else "live mode"
    estimate_text = (
        f"Estimated cost: up to ${estimate:.2f} per case ({mode_label}). "
        "The estimate is an upper bound (fiction cost is always included "
        "because the verdict is unknown at intake)."
    )
    return {
        "estimate_text": estimate_text,
        "estimated_cost_usd": estimate,
        "cost_mode": settings.analysis_mode,
        "expected_lineage_count": EXPECTED_LINEAGE_COUNT,
        "terms_short": (
            "No output from this service claims extraterrestrial origin. "
            "Story material is labelled speculation and must not be cited "
            "as evidence. Do not strip non-evidence labels. Verdicts are "
            "provisional and reflect only the tested mundane-explanation "
            "categories."
        ),
        "media_caps": {
            "image_mb": IMAGE_MAX_BYTES // (1024 * 1024),
            "video_mb": VIDEO_MAX_BYTES // (1024 * 1024),
            "image_mp": IMAGE_MAX_MP // 1_000_000,
            "video_max_s": int(VIDEO_MAX_DURATION_S),
        },
        # The backend validates free-text shape/count today; empty option
        # lists tell the SPA to render plain inputs (no invented enums).
        "field_options": {"direction": [], "shape": []},
        "estimated_benchmark_cost_usd": estimated_benchmark_cost_usd(settings),
    }


def benchmark_payload(conn, settings: Settings) -> dict:
    row = conn.execute(
        "SELECT * FROM benchmark_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    latest = None
    if row is not None:
        metrics = None
        if row["completed_at"]:
            try:
                recall = json.loads(row["per_category_recall_json"] or "{}")
            except Exception:
                recall = {}
            metrics = {
                "per_category_recall": recall,
                "false_no_mundane_match_rate":
                    row["false_no_mundane_match_rate"],
                "insufficient_detection_rate":
                    row["insufficient_detection_rate"],
            }
        latest = {
            "run_id": row["run_id"],
            "started_at": row["started_at"],
            "completed_at": row["completed_at"],
            "mode": row["mode"],
            "metrics": metrics,
            "error": None,
        }
    progress = None
    progress_file = settings.var_dir / "benchmark" / "progress.json"
    if progress_file.exists():
        try:
            prog = json.loads(progress_file.read_text(encoding="utf-8"))
            progress = {
                "completed": int(prog.get("completed", 0)),
                "total": int(prog.get("total", 0)),
                "done": bool(prog.get("done", False)),
                "error": prog.get("error"),
            }
        except Exception:
            progress = None
    prereg = None
    try:
        from uapvf import benchmark as benchmod

        raw = benchmod.load_prereg(settings)
        prereg = {
            "min_per_category_recall":
                raw.get("min_per_category_recall", 0.7),
            "max_false_no_mundane_match_rate":
                raw.get("max_false_no_mundane_match_rate", 0.15),
            "min_insufficient_detection_rate":
                raw.get("min_insufficient_detection_rate", 0.8),
            "prereg_hash": raw.get("prereg_hash"),
        }
    except Exception:
        prereg = None
    return {
        "latest_run": latest,
        "progress": progress,
        "prereg": prereg,
        "estimated_benchmark_cost_usd": estimated_benchmark_cost_usd(settings),
    }


def delete_confirm_payload(row) -> dict:
    return {
        "case_id": row["case_id"],
        "status": row["status"],
        "verdict": row["verdict"],
        "created_at": row["created_at"],
        "buyer_ref": row["buyer_ref"],
    }


def terms_payload(terms_items: list) -> dict:
    return {"terms_text": "\n\n".join(
        f"{i}. {item}" for i, item in enumerate(terms_items, 1))}


def fiction_payload(conn, case_id: str, row) -> dict:
    """Spec §3.9 fiction contract."""
    if row is None:
        return None  # caller 404s
    verdict = row["verdict"]
    if verdict == "mundane_identified":
        return {"content": None, "available": False, "withheld": False,
                "verdict": verdict}
    if not _fiction_on_disk(row):
        return {"content": None, "available": False,
                "withheld": _fiction_withheld(row),
                "verdict": verdict}
    try:
        content = Path(row["fiction_path"]).read_text(encoding="utf-8")
    except Exception:
        return {"content": None, "available": False, "withheld": False,
                "verdict": verdict}
    return {"content": content, "available": True, "withheld": False,
            "verdict": verdict}


# ---------------------------------------------------------------------------
# SPA shell serving (spec §3.5–§3.8)
# ---------------------------------------------------------------------------

CSP_HEADER = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; font-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; object-src 'none'; "
    "form-action 'self'"
)

_BOOTSTRAP_RE = re.compile(
    r'(<script id="uapv-bootstrap" type="application/json">)(.*?)(</script>)',
    re.S,
)

_shell_cache = {"mtime": None, "html": None}


def read_shell(ui_dist: Path) -> Optional[str]:
    """Cache index.html keyed on mtime (spec §3.5)."""
    index = ui_dist / "index.html"
    try:
        mtime = index.stat().st_mtime
    except OSError:
        return None
    if _shell_cache["mtime"] != mtime or _shell_cache["html"] is None:
        _shell_cache["html"] = index.read_text(encoding="utf-8")
        _shell_cache["mtime"] = mtime
    return _shell_cache["html"]


def render_shell(shell_html: str, authenticated: bool,
                 csrf_token: Optional[str], mode: str) -> str:
    bootstrap = {
        "authenticated": bool(authenticated),
        "csrf_token": csrf_token,
        "mode": mode,
    }
    payload = json.dumps(bootstrap).replace("<", "\\u003c")
    replaced, count = _BOOTSTRAP_RE.subn(
        lambda m: m.group(1) + payload + m.group(3), shell_html)
    if count == 0:
        # Degenerate build without the bootstrap element: append it so the
        # SPA never boots blind.
        replaced = shell_html.replace(
            "</body>",
            f'<script id="uapv-bootstrap" type="application/json">'
            f"{payload}</script></body>", 1)
    return replaced
