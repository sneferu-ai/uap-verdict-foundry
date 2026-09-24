"""Autonomous pipeline orchestration (FR-003, FR-004, FR-005, §3).

Stage sequence: intake -> quality_gate -> lineage_analysis ->
rigor_adjudication -> battery -> coverage_check -> verdict -> uncertainty ->
report -> fiction (FR-004: report renders first, then the seed for
unresolved cases; the report stage commits verdict_ready and the fiction
stage commits complete per the §3 state machine).

Durability contract (§3): stage outputs persist to pipeline_stage_runs
within a single transaction; filesystem artifacts are written temp -> fsync
-> rename BEFORE the commit; on restart analyzing cases reset to queued and
resume from committed stages (filtered by run_version); corrupt stage
outputs are audited and re-executed. External calls cannot be rolled back,
so duplicate spend from crash recovery is recorded with the
`duplicate_call_recovery` prefix.

Error taxonomy (FR-004): StageTransientError retries 3x with 1/2/4 s
backoff; StageUnrecoverableError fails the case immediately.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

from uapvf import audit, rigor, spend
from uapvf.adapters import BatteryConfigError
from uapvf.adapters.sneferu_adapter import (
    AdapterSchemaError,
    AdapterTimeout,
    fallback_fiction,
    generate_fiction,
    run_lineages,
)
from uapvf.battery import load_battery_results, persist_battery_results, run_battery
from uapvf.config import Settings, get_settings, utcnow_iso
from uapvf.intake import IntakeError, compute_warnings, parse_viewing_direction
from uapvf.lineages.protocol import LineageOutput, ProtocolError
from uapvf.lineages.registry import declared_lineage_ids
from uapvf.quality_gate import load_quality_config
from uapvf.render.fiction import (
    FICTION_MAX_BYTES,
    build_fiction_text,
    validate_fiction_text,
)
from uapvf.render.html_render import render_report_html
from uapvf.render.linter import lint_all
from uapvf.render.report_json import build_report_dict, serialize_report_json
from uapvf.verdict import compute_uncertainty, synthesize_verdict

log = logging.getLogger("uapvf.pipeline")

STAGES = [
    "intake",
    "quality_gate",
    "lineage_analysis",
    "rigor_adjudication",
    "battery",
    "coverage_check",
    "verdict",
    "uncertainty",
    "report",
    "fiction",
]

EXPECTED_BATTERY_CATEGORIES = (
    "aircraft", "satellites", "lens_artifacts", "astronomical"
)

REPORT_HTML_MAX_BYTES = 1_500_000  # 1.5 MB
REPORT_JSON_MAX_BYTES = 500_000    # 500 KB
TRANSIENT_RETRIES = 3
TRANSIENT_BACKOFF_S = (1.0, 2.0, 4.0)
MAX_RETRIES_DEFAULT = 5

MOCK_FAIL_NEXT = "mock_fail_next"
MOCK_PAUSE_AFTER_STAGE = "mock_pause_after_stage"


class StageTransientError(Exception):
    """Retryable stage failure (adapter timeout, 5xx, network)."""


class StageUnrecoverableError(Exception):
    """Terminal stage failure (schema mismatch, config error, size cap, lint)."""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _fsync_rename(tmp: Path, dst: Path) -> None:
    with open(tmp, "rb") as fh:
        try:
            os.fsync(fh.fileno())
        except Exception:
            pass
    tmp.rename(dst)


def _write_artifact(path: Path, data: bytes) -> str:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as fh:
        fh.write(data)
    _fsync_rename(tmp, path)
    import hashlib

    return hashlib.sha256(data).hexdigest()


def _case_fields(case) -> dict:
    try:
        return json.loads(case["fields_json"])
    except Exception:
        return {}


def _buyer_ref(conn, case_id: str):
    row = conn.execute(
        "SELECT buyer_ref FROM cases WHERE case_id = ?", (case_id,)
    ).fetchone()
    return row["buyer_ref"] if row else None


def _metadata_for(case) -> dict:
    fields = _case_fields(case)
    vd = None
    try:
        vd = parse_viewing_direction(fields.get("viewing_direction"))
    except Exception:
        vd = None
    return {
        "observed_at": case["observed_at"],
        "latitude": case["latitude"],
        "longitude": case["longitude"],
        "viewing_direction": vd,
        "location_text": case["location_text"] or fields.get("location_text"),
        "shape": fields.get("shape"),
        "count": fields.get("count"),
        "duration_seconds": fields.get("duration_seconds"),
        "weather": fields.get("weather"),
        "behavior_notes": fields.get("behavior_notes"),
    }


def _mock_hooks(settings: Settings):
    return {
        "fail_next": settings.var_dir / MOCK_FAIL_NEXT,
        "pause": settings.var_dir / MOCK_PAUSE_AFTER_STAGE,
    }


def _maybe_pause(settings: Settings, stage: str) -> None:
    """FR-018: pause after completing `stage` until the hook file is gone.
    The pause happens BEFORE the stage's transaction commits, so a crash
    here loses the stage row and restart re-executes it (AC-021)."""
    if not settings.mock_mode:
        return
    hook = settings.var_dir / MOCK_PAUSE_AFTER_STAGE
    try:
        if not hook.exists():
            return
        target = hook.read_text(encoding="utf-8").strip()
    except Exception:
        return
    if target != stage or stage not in STAGES:
        return
    while hook.exists():
        time.sleep(0.1)


def _consume_fail_next(settings: Settings) -> bool:
    """FR-018: existence triggers a failure of the next stage; the file is
    deleted after triggering."""
    if not settings.mock_mode:
        return False
    hook = settings.var_dir / MOCK_FAIL_NEXT
    if hook.exists():
        try:
            hook.unlink()
        except Exception:
            pass
        return True
    return False


def last_battery_results(conn, case_id: str) -> List[dict]:
    row = conn.execute(
        "SELECT MAX(run_version) AS v FROM battery_results WHERE case_id = ?",
        (case_id,),
    ).fetchone()
    version = row["v"] if row and row["v"] is not None else 1
    return load_battery_results(conn, case_id, version)


# ---------------------------------------------------------------------------
# Startup reconciliation (§3, §5 durable-state rules)
# ---------------------------------------------------------------------------

def reset_analyzing_on_startup(conn) -> int:
    """§3 restart contract: ``analyzing`` -> ``queued``. Also recovers
    ``verdict_ready``: that state is strictly transient (report commits it
    and the fiction stage completes the case in the same claim pass), so a
    case found in it after a restart was orphaned by a crash between the
    two stages. It resumes idempotently from committed stages (fiction
    pending) per the run_version resume contract."""
    rows = conn.execute(
        "SELECT case_id, status FROM cases WHERE status IN ('analyzing',"
        " 'verdict_ready')"
    ).fetchall()
    if not rows:
        return 0
    conn.execute(
        "UPDATE cases SET status = 'queued', updated_at = ? WHERE status IN"
        " ('analyzing', 'verdict_ready')",
        (utcnow_iso(),),
    )
    conn.commit()
    for r in rows:
        audit.append_event(conn, r["case_id"], "system", "startup_reset_to_queued",
                           {"previous_status": r["status"]})
    return len(rows)


def cleanup_orphan_tmp_files(settings: Settings) -> int:
    removed = 0
    cases_dir = settings.cases_dir
    if not cases_dir.exists():
        return 0
    seen = set()
    for pattern in ("*/**/*.tmp", "*/**/*.tmp.*", "*/*.tmp", "*/*.tmp.*"):
        for tmp in cases_dir.glob(pattern):
            if tmp in seen or not tmp.is_file():
                continue
            seen.add(tmp)
            try:
                tmp.unlink()
                removed += 1
            except Exception:
                pass
    return removed


def cleanup_uncommitted_case_dirs(settings: Settings, conn) -> int:
    """Remove UUID case roots left by a crash before the case INSERT."""
    import uuid

    known = {row["case_id"] for row in conn.execute("SELECT case_id FROM cases")}
    removed = 0
    if not settings.cases_dir.exists():
        return 0
    for child in settings.cases_dir.iterdir():
        if not child.is_dir() or child.name in known:
            continue
        try:
            uuid.UUID(child.name)
        except (ValueError, AttributeError):
            continue
        shutil.rmtree(child)
        removed += 1
    if removed:
        audit.append_event(conn, None, "system", "startup_orphan_cases_removed",
                           {"count": removed})
    return removed


# ---------------------------------------------------------------------------
# Claim logic (FR-003, FR-022)
# ---------------------------------------------------------------------------

def claim_case(conn, case_id: str, settings: Settings) -> Optional[dict]:
    """Atomically move a case to analyzing. Handles rerun_requested
    preparation (archive report, bump run_version, drop old results,
    audit verdict_superseded) and the claim-time spend recheck. Returns the
    fresh case row, or None when the claim was lost / cap blocked."""
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return None
    if row["status"] == "rerun_requested":
        old_version = row["run_version"]
        cur = conn.execute(
            "UPDATE cases SET status = 'analyzing', run_version = run_version + 1,"
            " updated_at = ?, current_stage = NULL, error_detail = NULL"
            " WHERE case_id = ? AND status = 'rerun_requested'",
            (utcnow_iso(), case_id),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
        _prepare_rerun(conn, case_id, row, old_version, settings)
    elif row["status"] == "queued":
        cur = conn.execute(
            "UPDATE cases SET status = 'analyzing', updated_at = ?, error_detail = NULL"
            " WHERE case_id = ? AND status = 'queued'",
            (utcnow_iso(), case_id),
        )
        conn.commit()
        if cur.rowcount != 1:
            return None
    else:
        return None

    fresh = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    # Spend recheck at claim time (benchmark seeds exempt, FR-016).
    estimate = settings.estimated_case_cost_usd()
    if not spend.spend_allows_case(conn, settings, estimate, fresh["buyer_ref"]):
        conn.execute(
            "UPDATE cases SET status = 'spend_capped', updated_at = ? WHERE case_id = ?",
            (utcnow_iso(), case_id),
        )
        conn.commit()
        audit.append_event(conn, case_id, "system", "spend_cap_blocked",
                           {"estimated_cost_usd": estimate,
                            "cap_usd": float(settings.UAPV_SPEND_CAP_USD)})
        return None
    return fresh


def _prepare_rerun(conn, case_id: str, old_row, old_version: int,
                   settings: Settings) -> None:
    case_dir = settings.cases_dir / case_id
    old_report = case_dir / "report.html"
    if old_report.exists():
        archive = case_dir / f"report_{case_id}_v{old_version}.html"
        try:
            shutil.copyfile(old_report, archive)
        except Exception:
            pass
    conn.execute(
        "DELETE FROM battery_results WHERE case_id = ? AND run_version = ?",
        (case_id, old_version),
    )
    conn.execute(
        "DELETE FROM lineage_outputs WHERE case_id = ? AND run_version = ?",
        (case_id, old_version),
    )
    conn.commit()
    audit.append_event(
        conn, case_id, "system", "verdict_superseded",
        {
            "old_verdict": old_row["verdict"],
            "old_run_version": old_version,
            "new_run_version": old_version + 1,
        },
    )


def release_spend_capped_if_rollover(conn, settings: Settings) -> int:
    """Automatic spend_capped -> queued on UTC month rollover when budget
    allows (worker checks every poll cycle, FR-003/FR-016)."""
    rows = conn.execute(
        "SELECT case_id, buyer_ref FROM cases WHERE status = 'spend_capped'"
    ).fetchall()
    released = 0
    estimate = settings.estimated_case_cost_usd()
    for r in rows:
        if spend.spend_allows_case(conn, settings, estimate, r["buyer_ref"]):
            conn.execute(
                "UPDATE cases SET status = 'queued', updated_at = ? WHERE case_id = ?",
                (utcnow_iso(), r["case_id"]),
            )
            conn.commit()
            audit.append_event(conn, r["case_id"], "system", "spend_cap_released",
                               {"month": spend.current_month()})
            released += 1
    return released


# ---------------------------------------------------------------------------
# Stage implementations. Each returns its output dict.
# ---------------------------------------------------------------------------

def _stage_intake(conn, case, settings, context) -> dict:
    fields = _case_fields(case)
    media_path = case["media_path"]
    if not media_path or not Path(media_path).exists():
        raise StageUnrecoverableError("normalized media missing from case directory")
    kind = "video" if Path(media_path).suffix == ".mp4" else "image"
    if case["media_kind"] != kind:
        conn.execute(
            "UPDATE cases SET media_kind=?, updated_at=? WHERE case_id=?",
            (kind, utcnow_iso(), case["case_id"]),
        )
        conn.commit()
    original_path = None
    parent = Path(media_path).parent
    for cand in parent.iterdir():
        if cand.name.startswith("original"):
            original_path = cand
            break
    warnings = []
    warning_event = conn.execute(
        "SELECT detail_json FROM audit_events WHERE case_id=? AND "
        "action='intake_warnings' ORDER BY event_id DESC LIMIT 1",
        (case["case_id"],),
    ).fetchone()
    if warning_event:
        try:
            warnings = json.loads(warning_event["detail_json"]).get("warnings") or []
        except Exception:
            warnings = []
    if not warnings and case["latitude"] == 0.0 and case["longitude"] == 0.0:
        warnings = [{"type": "null_island",
                     "message": "coordinates at null island; verify accuracy"}]
    # NOTE: create_case already audited intake_warnings at submission time;
    # the stage output carries them for resume paths without re-auditing.
    context["warnings"] = warnings
    return {
        "warnings": warnings,
        "media_path": media_path,
        "kind": kind,
    }


def _stage_quality_gate(conn, case, settings, context) -> dict:
    # The hostile original was decoded and scored inside the one-shot intake
    # sandbox before the case row was committed. Reuse that persisted result;
    # never decode the original again in the long-lived worker process.
    qcfg = load_quality_config(settings)
    return {
        "quality_score": float(case["quality_score"] or 0.0),
        "quality_score_normalized": float(case["quality_score"] or 0.0),
        "quality_gate_pass": bool(case["quality_gate_pass"]),
        "vision_quality_floor": float(qcfg.get("vision_quality_floor", 0.35)),
        "source": "case_scoped_intake_sandbox",
    }


def _stage_lineage_analysis(conn, case, settings, context) -> dict:
    case_id = case["case_id"]
    run_version = case["run_version"]
    quality = context.get("stage_outputs", {}).get("quality_gate", {})
    gate_pass = quality.get(
        "quality_gate_pass", bool(case["quality_gate_pass"])
    )
    if not gate_pass:
        out = {
            "lineages": [],
            "lineage_count": 0,
            "agreement_fraction": 0.0,
            "rigor": None,
            "skipped": True,
            "reason": "media quality below vision threshold",
        }
        _persist_lineage_state(conn, case_id, out)
        return out
    metadata = _metadata_for(case)
    try:
        # FR-005: lineage analysis ALWAYS goes through the Sneferu adapter —
        # deterministic fixtures in mock mode, orchestration-engine
        # submission otherwise. Local lineage execution and subprocess-level
        # network isolation are deferred to phase 2 (spec §1 deferred
        # table; spec.md:85), so no live path runs local lineage modules.
        lineages = run_lineages(case_id, case["media_path"], metadata, settings)
    except AdapterSchemaError as exc:
        raise StageUnrecoverableError(f"invalid adapter response schema: {exc}")
    except AdapterTimeout as exc:
        raise StageTransientError(str(exc))
    if not lineages:
        out = {
            "lineages": [],
            "lineage_count": 0,
            "agreement_fraction": 0.0,
            "rigor": None,
            "skipped": True,
            "reason": "no vision lineages available",
        }
        _persist_lineage_state(conn, case_id, out)
        return out
    _record_lineage_spend(conn, case_id, lineages, settings)
    try:
        rigor_out = compute_lineage_rigor(lineages, settings)
    except rigor.RigorConfigError as exc:
        raise StageUnrecoverableError(f"rigor configuration unusable: {exc}")
    # FR-005 plurality ratio: (count of the most common classification
    # label) / (total lineage count). Every enum label counts — the
    # spec's worked example scores 3 lineages labelled
    # ["unknown", "unknown", "unknown"] as 3/3 = 1.0, and the mock
    # contract (FR-005 / AC-004) stores agreement_fraction = 1.0 for the
    # flagship journey. A missing label (None — rejected by the live
    # response schema validator, never produced by the mock fixture)
    # cannot form a plurality but still counts in the denominator, and
    # 0/1 lineages define agreement as 0.0.
    labels = [l.get("classification") for l in lineages
              if l.get("classification") is not None]
    counts = Counter(labels)
    if len(lineages) >= 2 and counts:
        agreement = counts.most_common(1)[0][1] / len(lineages)
    else:
        agreement = 0.0
    if rigor_out is not None:
        # §3.3 concordance supersedes the majority-vote fraction.
        agreement = rigor_out["agreement_fraction"]
        audit.append_event(
            conn, case_id, "system", "rigor_evaluated",
            {
                "runtime": rigor_out["runtime"],
                "passed": rigor_out["passed"],
                "reason": rigor_out["reason"],
                "agreement_fraction": rigor_out["agreement_fraction"],
                "variance": rigor_out["variance"],
                "plurality": rigor_out["plurality"],
                "plurality_count": rigor_out["plurality_count"],
                "conditions": rigor_out["conditions"],
                "operator_gates": rigor_out["operator_gates"],
            },
        )
    out = {
        "lineages": lineages,
        "lineage_count": len(lineages),
        "agreement_fraction": round(agreement, 6),
        "rigor": rigor_out,
        "sandbox": None,
        "skipped": False,
        "reason": None,
    }
    _persist_lineage_state(conn, case_id, out)
    for l in lineages:
        conn.execute(
            "INSERT OR REPLACE INTO lineage_outputs "
            "(case_id, lineage_id, run_version, hypothesis, classification,"
            " artifact_detected, recorded_at, status, evidence_claims_json,"
            " confidence, provenance_json, abstention_reason) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                case_id, l["lineage_id"], run_version,
                l.get("hypothesis") or l.get("rationale"),
                l.get("classification"),
                1 if l.get("artifact_detected") else 0, utcnow_iso(),
                l.get("status"),
                json.dumps(l.get("evidence_claims") or {}, sort_keys=True),
                l.get("confidence"),
                json.dumps(l.get("provenance") or {}, sort_keys=True),
                l.get("abstention_reason"),
            ),
        )
    conn.execute(
        "UPDATE cases SET sandbox_verified = ?, lineage_runtime = ?, "
        "updated_at = ? WHERE case_id = ?",
        # Subprocess-level network isolation is deferred to phase 2
        # (spec §1 deferred table), so no MVP case is sandbox-verified.
        (0, settings.analysis_mode, utcnow_iso(), case_id),
    )
    conn.commit()
    return out


def _persist_lineage_state(conn, case_id: str, out: dict) -> None:
    conn.execute(
        "UPDATE cases SET lineage_count = ?, agreement_fraction = ?, updated_at = ?"
        " WHERE case_id = ?",
        (out["lineage_count"], out["agreement_fraction"], utcnow_iso(), case_id),
    )
    conn.commit()


def _record_lineage_spend(conn, case_id: str, lineages: list,
                          settings: Settings) -> None:
    if spend.is_benchmark_case(_buyer_ref(conn, case_id)):
        return
    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM spend_entries WHERE case_id = ? AND "
        "(description LIKE 'lineage:%' OR description LIKE 'duplicate_call_recovery:lineage:%')",
        (case_id,),
    ).fetchone()["n"]
    prefix = "duplicate_call_recovery:" if existing else ""
    for l in lineages:
        spend.record_spend(
            conn, case_id, settings.lineage_cost_usd,
            f"{prefix}lineage:{l['lineage_id']}",
        )


# ---------------------------------------------------------------------------
# Seven-lineage concordance call site (spec §3.3/§3.4)
# ---------------------------------------------------------------------------

def _parse_lineage_contract(lineages: list):
    """Parse lineage dicts into ``LineageOutput`` objects (spec §3.2
    contract). Returns None when the outputs predate the contract (mock /
    legacy HTTP engine dicts), name undeclared lineage ids, or duplicate a
    lineage — the caller then keeps the legacy agreement computation."""
    if not lineages:
        return None
    declared = set(declared_lineage_ids())
    outputs = []
    for item in lineages:
        if not isinstance(item, dict):
            return None
        try:
            out = LineageOutput.from_dict(item)
        except ProtocolError:
            return None
        if out.lineage_id not in declared:
            return None
        outputs.append(out)
    ids = [o.lineage_id for o in outputs]
    if len(set(ids)) != len(ids):
        return None
    return outputs


def compute_lineage_rigor(lineages: list, settings: Settings) -> Optional[dict]:
    """Concordance at the pipeline call site (spec §3.3): score
    seven-lineage-contract outputs with the classification-gated
    refutation-consistent metric.

    Live mode derives the condition 4/5 booleans from
    ``rigor.evaluate_operator_gates(settings.var_dir)`` — fail-closed
    False when the stored operator certification artifacts are absent or
    sub-protocol (spec §3.2 pt 8). The mock runtime is blocked outright
    by ``rigor_config.json`` (as is any runtime the shipped conditions
    table does not name — the removed ``simulation`` runtime can no
    longer select a relaxed gate set), so the gates are live-mode inputs;
    blocked runtimes record the reason for auditability. Returns None for
    pre-contract lineage dicts (the legacy majority-vote
    agreement_fraction applies).
    """
    outputs = _parse_lineage_contract(lineages)
    if outputs is None:
        return None
    runtime = settings.lineage_runtime
    if runtime == rigor.RUNTIME_LIVE:
        gates = rigor.evaluate_operator_gates(settings.var_dir)
    else:
        gates = rigor.OperatorGates(
            independence_validated=False,
            calibration_valid=False,
            independence_reason=f"runtime_{runtime}",
            calibration_reason=f"runtime_{runtime}",
        )
    result = rigor.compute_concordance(
        outputs,
        runtime=runtime,
        independence_validated=gates.independence_validated,
        calibration_valid=gates.calibration_valid,
    )
    out = result.to_dict()
    out["operator_gates"] = gates.to_dict()
    return out


def _stage_battery(conn, case, settings, context) -> dict:
    lineage_out = context.get("stage_outputs", {}).get("lineage_analysis", {})
    lineage_outputs = lineage_out.get("lineages") or []
    metadata = _metadata_for(case)
    try:
        results = run_battery(
            case["case_id"], case["media_path"], metadata, lineage_outputs
        )
    except BatteryConfigError as exc:
        raise StageUnrecoverableError(str(exc))
    except AdapterTimeout as exc:
        raise StageTransientError(f"battery adapter timeout: {exc}")
    quality = context.get("stage_outputs", {}).get("quality_gate", {})
    if not quality.get("quality_gate_pass", True):
        # FR-002: vision-dependent categories (the lens-artifact family)
        # return insufficient with the quality reason, which takes
        # precedence over the zero-lineage reason. Metadata-only
        # categories (aircraft, satellites, astronomical) still execute.
        vision_categories = {
            "lens_artifacts", "sensor_artifacts",
        }
        for r in results:
            if r["category"] in vision_categories:
                r["result"] = "insufficient"
                r["evidence_citation"] = None
                r["source_stamp"] = {
                    "source_id": "lineage_unavailable",
                    "query_params": "{}",
                    "utc_timestamp": utcnow_iso(),
                    "content_hash": "unavailable",
                    "source_version": "lineage_v1",
                    "reason": "media quality below vision threshold",
                }
    persist_battery_results(conn, case["case_id"], case["run_version"], results)
    return {"results": results}


def _stage_rigor_adjudication(conn, case, settings, context) -> dict:
    lineage_out = context.get("stage_outputs", {}).get("lineage_analysis", {})
    result = lineage_out.get("rigor")
    if not isinstance(result, dict):
        result = {
            "runtime": settings.analysis_mode,
            "agreement_fraction": float(lineage_out.get("agreement_fraction") or 0.0),
            "variance": 0.0,
            "plurality": None,
            "plurality_count": 0,
            "conditions": {},
            "refutation": {"challenges": [], "refuted": [], "passed": False},
            "passed": False,
            "reason": "seven_lineage_contract_unavailable",
        }
    conn.execute(
        "INSERT OR REPLACE INTO rigor_runs "
        "(case_id, run_version, runtime, agreement_fraction, population_variance,"
        " plurality, plurality_count, conditions_json, refutation_json, passed,"
        " reason, recorded_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (case["case_id"], case["run_version"], result.get("runtime") or
         settings.analysis_mode, float(result.get("agreement_fraction") or 0.0),
         float(result.get("variance") or 0.0), result.get("plurality"),
         int(result.get("plurality_count") or 0),
         json.dumps(result.get("conditions") or {}, sort_keys=True),
         json.dumps(result.get("refutation") or {}, sort_keys=True),
         1 if result.get("passed") else 0,
         str(result.get("reason") or "unspecified"), utcnow_iso()),
    )
    conn.commit()
    return result


def _stage_coverage_check(conn, case, settings, context) -> dict:
    battery_out = context.get("stage_outputs", {}).get("battery", {})
    results = battery_out.get("results") or last_battery_results(
        conn, case["case_id"])
    by_category = {item.get("category"): item for item in results}
    tested = sorted(
        category for category, item in by_category.items()
        if item.get("result") in ("positive", "negative")
    )
    insufficient = sorted(
        category for category, item in by_category.items()
        if item.get("result") == "insufficient"
    )
    missing = sorted(set(EXPECTED_BATTERY_CATEGORIES) - set(by_category))
    coverage = {}
    for category in ("weather", "balloons"):
        stamp = (by_category.get(category) or {}).get("source_stamp") or {}
        coverage[category] = stamp.get("geographic_coverage", "unavailable")
    complete = not missing and not insufficient
    out = {
        "expected_categories": list(EXPECTED_BATTERY_CATEGORIES),
        "tested_categories": tested,
        "insufficient_categories": insufficient,
        "missing_categories": missing,
        "coverage": coverage,
        "complete": complete,
    }
    conn.execute(
        "INSERT OR REPLACE INTO case_coverage "
        "(case_id, run_version, expected_categories_json, tested_categories_json,"
        " insufficient_categories_json, complete, recorded_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (case["case_id"], case["run_version"],
         json.dumps(out["expected_categories"]), json.dumps(tested),
         json.dumps(insufficient + missing), 1 if complete else 0, utcnow_iso()),
    )
    conn.execute(
        "UPDATE cases SET coverage_complete = ?, updated_at = ? WHERE case_id = ?",
        (1 if complete else 0, utcnow_iso(), case["case_id"]),
    )
    conn.commit()
    return out


def _stage_verdict(conn, case, settings, context) -> dict:
    battery_out = context.get("stage_outputs", {}).get("battery", {})
    results = battery_out.get("results") or last_battery_results(conn, case["case_id"])
    lineage_out = context.get("stage_outputs", {}).get("lineage_analysis", {})
    lineage_outputs = lineage_out.get("lineages") or []
    coverage_out = context.get("stage_outputs", {}).get("coverage_check", {})
    rigor_out = context.get("stage_outputs", {}).get("rigor_adjudication")
    verdict = synthesize_verdict(
        results, lineage_outputs,
        rigor_result=rigor_out or lineage_out.get("rigor"),
        coverage=coverage_out.get("coverage") or battery_out.get("coverage"),
    )
    conn.execute(
        "UPDATE cases SET verdict = ?, verdict_text = ?, updated_at = ? "
        "WHERE case_id = ?",
        (verdict["verdict"], verdict["verdict_text"], utcnow_iso(), case["case_id"]),
    )
    conn.commit()
    audit.append_event(conn, case["case_id"], "system", "verdict_set",
                       {"verdict": verdict["verdict"], "reason": verdict["reason"],
                        "qualifications": verdict.get("qualifications") or []})
    return verdict


def _stage_uncertainty(conn, case, settings, context) -> dict:
    battery_out = context.get("stage_outputs", {}).get("battery", {})
    results = battery_out.get("results") or last_battery_results(conn, case["case_id"])
    lineage_out = context.get("stage_outputs", {}).get("lineage_analysis", {})
    row = conn.execute(
        "SELECT verdict, agreement_fraction, lineage_count, quality_score "
        "FROM cases WHERE case_id = ?", (case["case_id"],)
    ).fetchone()
    uncertainty = compute_uncertainty(
        row["verdict"],
        results,
        row["agreement_fraction"] or 0.0,
        row["lineage_count"] or 0,
        row["quality_score"] or 0.0,
        settings,
    )
    cal_source = uncertainty.get("calibration_source")
    conn.execute(
        "UPDATE cases SET uncertainty = ?, uncertainty_calibrated = ?,"
        " uncertainty_reason = ?, calibration_source = ?, updated_at = ?"
        " WHERE case_id = ?",
        (
            uncertainty.get("value"),
            1 if uncertainty.get("calibrated") else 0,
            uncertainty.get("uncertainty_reason"),
            json.dumps(cal_source, sort_keys=True) if cal_source else None,
            utcnow_iso(),
            case["case_id"],
        ),
    )
    conn.commit()
    return uncertainty


def _stage_report(conn, case, settings, context) -> dict:
    case_id = case["case_id"]
    case_dir = settings.cases_dir / case_id
    case_dir.mkdir(parents=True, exist_ok=True)
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    battery_results = last_battery_results(conn, case_id)
    lineage_rows = conn.execute(
        "SELECT * FROM lineage_outputs WHERE case_id = ? AND run_version = ?"
        " ORDER BY lineage_id",
        (case_id, row["run_version"]),
    ).fetchall()
    def _parsed(value, fallback):
        try:
            return json.loads(value) if value else fallback
        except Exception:
            return fallback

    lineage_outputs = []
    for r in lineage_rows:
        lineage_outputs.append({
            "lineage_id": r["lineage_id"],
            "classification": r["classification"],
            "artifact_detected": bool(r["artifact_detected"]),
            "hypothesis": r["hypothesis"],
            "model_version": "lineage_v1",
            "status": r["status"] or "unknown",
            "confidence": r["confidence"],
            "evidence_claims": _parsed(r["evidence_claims_json"], []),
            "provenance": _parsed(r["provenance_json"], {}),
            "abstention_reason": r["abstention_reason"],
        })
    rigor_row = conn.execute(
        "SELECT * FROM rigor_runs WHERE case_id=? AND run_version=?",
        (case_id, row["run_version"]),
    ).fetchone()
    coverage_row = conn.execute(
        "SELECT * FROM case_coverage WHERE case_id=? AND run_version=?",
        (case_id, row["run_version"]),
    ).fetchone()
    rigor = ({
        "runtime": rigor_row["runtime"],
        "agreement_fraction": rigor_row["agreement_fraction"],
        "population_variance": rigor_row["population_variance"],
        "plurality": rigor_row["plurality"],
        "plurality_count": rigor_row["plurality_count"],
        "conditions": _parsed(rigor_row["conditions_json"], {}),
        "refutation": _parsed(rigor_row["refutation_json"], {}),
        "passed": bool(rigor_row["passed"]),
        "reason": rigor_row["reason"],
    } if rigor_row else {})
    coverage = ({
        "expected_categories": _parsed(coverage_row["expected_categories_json"], []),
        "tested_categories": _parsed(coverage_row["tested_categories_json"], []),
        "insufficient_categories": _parsed(
            coverage_row["insufficient_categories_json"], []),
        "complete": bool(coverage_row["complete"]),
    } if coverage_row else {})
    from uapvf.references import resolve as resolve_references
    phase2 = {
        "lineage_runtime": row["lineage_runtime"] or settings.analysis_mode,
        "sandbox_verified": bool(row["sandbox_verified"]),
        "rigor": rigor,
        "coverage": coverage,
        "references": resolve_references(settings),
    }
    uncertainty_out = context.get("stage_outputs", {}).get("uncertainty", {})
    if not uncertainty_out:
        uncertainty_out = {
            "raw_confidence": 0.0, "penalty": 0.0, "value": row["uncertainty"],
            "calibrated": bool(row["uncertainty_calibrated"]),
            "calibration_source": None, "low_confidence": False,
            "uncertainty_reason": row["uncertainty_reason"]
            or "no calibration run available",
        }
    warnings = context.get("warnings") or context.get("stage_outputs", {}).get(
        "intake", {}
    ).get("warnings", [])

    report_dict = build_report_dict(row, battery_results, lineage_outputs,
                                    uncertainty_out, warnings, phase2)
    html_text = render_report_html(row, battery_results, lineage_outputs,
                                   uncertainty_out, warnings, phase2)
    json_text = serialize_report_json(report_dict)

    html_bytes = html_text.encode("utf-8")
    json_bytes = json_text.encode("utf-8")
    if len(html_bytes) > REPORT_HTML_MAX_BYTES or len(json_bytes) > REPORT_JSON_MAX_BYTES:
        raise StageUnrecoverableError("report size cap exceeded")
    lint = lint_all(html_text, report_dict)
    if not lint["ok"]:
        raise StageUnrecoverableError(
            "export lint violation: " + "; ".join(lint["violations"][:5])
        )
    # temp -> fsync -> rename BEFORE the DB commit (§3).
    html_sha = _write_artifact(case_dir / "report.html", html_bytes)
    json_sha = _write_artifact(case_dir / "report.json", json_bytes)
    conn.execute(
        "UPDATE cases SET report_path = ?, report_sha256 = ?, report_json_sha256 = ?,"
        " status = 'verdict_ready', updated_at = ? WHERE case_id = ?",
        (
            str(case_dir / "report.html"), html_sha, json_sha, utcnow_iso(), case_id,
        ),
    )
    conn.commit()
    audit.append_event(conn, case_id, "system", "report_rendered",
                       {"report_sha256": html_sha, "report_json_sha256": json_sha})
    return {
        "report_path": str(case_dir / "report.html"),
        "report_json_path": str(case_dir / "report.json"),
        "report_sha256": html_sha,
        "report_json_sha256": json_sha,
    }


def _complete_case(conn, case_id: str) -> None:
    """§3 state machine: verdict_ready -> complete once fiction generation
    has been attempted (success, fallback, or cleared)."""
    conn.execute(
        "UPDATE cases SET status = 'complete', updated_at = ? WHERE case_id = ?",
        (utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "system", "case_complete",
                       {"pipeline_stages": len(STAGES)})


def _stage_fiction(conn, case, settings, context) -> dict:
    case_id = case["case_id"]
    case_dir = settings.cases_dir / case_id
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    verdict = row["verdict"]
    if verdict == "mundane_identified":
        # No fiction for resolved cases; a rerun may have left an old seed.
        if row["fiction_path"] and Path(row["fiction_path"]).exists():
            try:
                Path(row["fiction_path"]).unlink()
            except Exception:
                pass
        conn.execute(
            "UPDATE cases SET fiction_path = NULL, fiction_sha256 = NULL,"
            " fiction_ready = 1, updated_at = ? WHERE case_id = ?",
            (utcnow_iso(), case_id),
        )
        conn.commit()
        _complete_case(conn, case_id)
        return {"fiction_path": None, "fiction_sha256": None, "skipped": True,
                "reason": "resolved case"}

    metadata = _metadata_for(case)
    metadata["case_id"] = case_id
    paragraphs = None
    attempts = []
    for strict in (False, True):
        try:
            payload = generate_fiction(case_id, metadata, settings, strict=strict)
            candidate = build_fiction_text(case_id, payload["paragraphs"])
            ok, reasons = validate_fiction_text(candidate)
            attempts.append({"attempt": "generate", "strict": strict, "ok": ok,
                             "reasons": reasons,
                             "source": payload.get("source", "configured-seed")})
            if ok:
                paragraphs = candidate
                break
        except AdapterTimeout as exc:
            attempts.append({"attempt": "generate", "strict": strict,
                             "ok": False, "reasons": [str(exc)]})
        except Exception as exc:
            attempts.append({"attempt": "generate", "strict": strict,
                             "ok": False, "reasons": [str(exc)]})
    if paragraphs is None:
        # Retry by re-formatting raw text (FR-012), then the fallback
        # formatter (FR-011).
        try:
            raw = fallback_fiction(metadata)
            candidate = build_fiction_text(case_id, raw)
            ok, reasons = validate_fiction_text(candidate)
            attempts.append({"attempt": "fallback", "ok": ok, "reasons": reasons})
            if ok:
                paragraphs = candidate
        except Exception as exc:
            attempts.append({"attempt": "fallback", "ok": False, "reasons": [str(exc)]})

    if paragraphs is None:
        conn.execute(
            "UPDATE cases SET fiction_path = NULL, fiction_sha256 = NULL,"
            " fiction_ready = 1, updated_at = ? WHERE case_id = ?",
            (utcnow_iso(), case_id),
        )
        conn.commit()
        audit.append_event(conn, case_id, "system", "fiction_label_validation_failed",
                           {"attempts": attempts})
        _complete_case(conn, case_id)
        return {"fiction_path": None, "fiction_sha256": None, "skipped": True,
                "reason": "label validation failed after retry"}

    fiction_path = case_dir / f"fiction_{case_id}.fic.md"
    fiction_sha = _write_artifact(fiction_path, paragraphs.encode("utf-8"))
    _record_fiction_spend(conn, case_id, settings, attempts)
    conn.execute(
        "UPDATE cases SET fiction_path = ?, fiction_sha256 = ?, fiction_ready = 1,"
        " updated_at = ? WHERE case_id = ?",
        (str(fiction_path), fiction_sha, utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "system", "fiction_generated",
                       {"fiction_sha256": fiction_sha, "attempts": attempts})
    _complete_case(conn, case_id)
    return {"fiction_path": str(fiction_path), "fiction_sha256": fiction_sha,
            "skipped": False, "reason": None}


def _record_fiction_spend(conn, case_id: str, settings: Settings,
                          attempts: list) -> None:
    if spend.is_benchmark_case(_buyer_ref(conn, case_id)):
        return
    last = attempts[-1] if attempts else {}
    if last.get("attempt") == "fallback":
        if not settings.mock_mode:
            # FR-016: per-case compute costs come from SDK calls ONLY. A
            # seed produced by the deterministic local fallback formatter
            # made no engine call, so it books $0 — charging the cap for
            # a local format() was phantom spend.
            return
        source = "fallback"
    else:
        # A successful generate attempt: an engine call in live mode
        # (source="engine"), a simulated SDK fixture in mock mode.
        source = "seed"
    existing = conn.execute(
        "SELECT COUNT(*) AS n FROM spend_entries WHERE case_id = ? AND "
        "(description LIKE 'fiction%' OR description LIKE 'duplicate_call_recovery:fiction%')",
        (case_id,),
    ).fetchone()["n"]
    prefix = "duplicate_call_recovery:" if existing else ""
    spend.record_spend(conn, case_id, settings.fiction_cost_usd,
                       f"{prefix}fiction:{source}")


STAGE_FUNCS = {
    "intake": _stage_intake,
    "quality_gate": _stage_quality_gate,
    "lineage_analysis": _stage_lineage_analysis,
    "rigor_adjudication": _stage_rigor_adjudication,
    "battery": _stage_battery,
    "coverage_check": _stage_coverage_check,
    "verdict": _stage_verdict,
    "uncertainty": _stage_uncertainty,
    "report": _stage_report,
    "fiction": _stage_fiction,
}


# ---------------------------------------------------------------------------
# Case processing
# ---------------------------------------------------------------------------

def _load_completed_stages(conn, case_id: str, run_version: int) -> dict:
    rows = conn.execute(
        "SELECT * FROM pipeline_stage_runs WHERE case_id = ? AND run_version = ?"
        " AND status = 'completed'",
        (case_id, run_version),
    ).fetchall()
    out = {}
    for r in rows:
        try:
            output = json.loads(r["output_json"]) if r["output_json"] else {}
        except Exception:
            audit.append_event(
                conn, case_id, "system", "stage_resume_corrupt",
                {"stage": r["stage"], "run_version": run_version},
            )
            continue
        out[r["stage"]] = output
    return out


def _record_stage(conn, case_id: str, run_version: int, stage: str,
                  status: str, output=None, error: Optional[str] = None,
                  started_at: Optional[str] = None,
                  artifact_hash: Optional[str] = None) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO pipeline_stage_runs "
        "(case_id, stage, status, output_json, artifact_hash, started_at,"
        " completed_at, error_detail, run_version)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            case_id, stage, status,
            json.dumps(output, sort_keys=True, ensure_ascii=False) if output is not None else None,
            artifact_hash,
            started_at or utcnow_iso(),
            utcnow_iso() if status in ("completed", "failed") else None,
            error,
            run_version,
        ),
    )
    conn.commit()


def process_one_case(case_id: str, settings: Optional[Settings] = None,
                     conn=None) -> dict:
    """Claim and drive one case to a terminal state. Returns
    {case_id, status, stage_outputs}."""
    from uapvf import db as dbmod

    settings = settings or get_settings()
    own_conn = conn is None
    if own_conn:
        conn = dbmod.connect(settings.db_path)
    stage_outputs: dict = {}
    try:
        case = claim_case(conn, case_id, settings)
        if case is None:
            row = conn.execute(
                "SELECT status FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
            return {"case_id": case_id,
                    "status": row["status"] if row else "missing",
                    "stage_outputs": {}}
        run_version = case["run_version"]
        completed = _load_completed_stages(conn, case_id, run_version)
        context = {"stage_outputs": stage_outputs, "warnings": []}
        # Restore warnings from a committed intake stage (resume path).
        if "intake" in completed:
            context["warnings"] = completed["intake"].get("warnings", [])

        for stage in STAGES:
            conn.execute(
                "UPDATE cases SET current_stage = ?, updated_at = ? WHERE case_id = ?",
                (stage, utcnow_iso(), case_id),
            )
            conn.commit()
            if stage in completed:
                stage_outputs[stage] = completed[stage]
                if stage == "intake":
                    context["warnings"] = completed[stage].get("warnings", [])
                continue
            if _consume_fail_next(settings):
                error = "mock induced stage failure (var/mock_fail_next)"
                _record_stage(conn, case_id, run_version, stage, "failed",
                              error=error)
                _fail_case(conn, case_id, error)
                return {"case_id": case_id, "status": "failed",
                        "stage_outputs": stage_outputs}
            output = _execute_stage_with_retry(
                conn, case, settings, stage, context, stage_outputs
            )
            if output is _FAILED:
                row = conn.execute(
                    "SELECT error_detail FROM cases WHERE case_id = ?", (case_id,)
                ).fetchone()
                return {"case_id": case_id, "status": "failed",
                        "stage_outputs": stage_outputs,
                        "failed_stage": stage,
                        "error": row["error_detail"] if row else None}
            _maybe_pause(settings, stage)
            artifact_hash = None
            if isinstance(output, dict):
                artifact_hash = output.get("report_sha256") or output.get(
                    "fiction_sha256"
                )
            _record_stage(conn, case_id, run_version, stage, "completed",
                          output=output, artifact_hash=artifact_hash)
            if isinstance(output, dict) and output.get("skipped"):
                audit.append_event(
                    conn, case_id, "system", "stage_skipped",
                    {"stage": stage, "reason": output.get("reason")},
                )
            stage_outputs[stage] = output
            if stage == "intake":
                context["warnings"] = output.get("warnings", [])
            # The case row may have advanced (report -> verdict_ready,
            # fiction -> complete); refresh for the next stage.
            case = conn.execute(
                "SELECT * FROM cases WHERE case_id = ?", (case_id,)
            ).fetchone()
        row = conn.execute(
            "SELECT status FROM cases WHERE case_id = ?", (case_id,)
        ).fetchone()
        return {"case_id": case_id, "status": row["status"],
                "stage_outputs": stage_outputs}
    finally:
        if own_conn:
            conn.close()


_FAILED = object()


def _execute_stage_with_retry(conn, case, settings, stage, context,
                              stage_outputs):
    case_id = case["case_id"]
    run_version = case["run_version"]
    func = STAGE_FUNCS[stage]
    audit.append_event(conn, case_id, "system", "stage_started",
                       {"stage": stage, "run_version": run_version})
    attempt = 0
    last_error = None
    while attempt <= TRANSIENT_RETRIES:
        started = utcnow_iso()
        try:
            output = func(conn, case, settings, context)
            audit.append_event(conn, case_id, "system", "stage_completed",
                               {"stage": stage, "run_version": run_version})
            return output
        except StageUnrecoverableError as exc:
            last_error = str(exc)
            break
        except StageTransientError as exc:
            last_error = str(exc)
            attempt += 1
            if attempt > TRANSIENT_RETRIES:
                break
            time.sleep(TRANSIENT_BACKOFF_S[min(attempt - 1, 2)])
        except Exception as exc:  # logic errors are unrecoverable (FR-004)
            last_error = f"{exc.__class__.__name__}: {exc}"
            break
    _record_stage(conn, case_id, run_version, stage, "failed", error=last_error,
                  started_at=started)
    _fail_case(conn, case_id, last_error or "unknown stage failure", stage)
    return _FAILED


def _fail_case(conn, case_id: str, error: str, stage: Optional[str] = None) -> None:
    conn.execute(
        "UPDATE cases SET status = 'failed', error_detail = ?, updated_at = ?"
        " WHERE case_id = ?",
        (error[:4000], utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "system", "case_failed",
                       {"error": error, "stage": stage})


# ---------------------------------------------------------------------------
# Operator actions (FR-019, FR-021, FR-022, FR-025)
# ---------------------------------------------------------------------------

def retry_case(conn, case_id: str, force: bool = False) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "case not found"}
    if row["status"] != "failed":
        return {"ok": False, "error": "case is not in failed state"}
    if row["retry_count"] >= MAX_RETRIES_DEFAULT and not force:
        return {"ok": False, "error": "max retries exceeded; use --force to override"}
    new_count = 0 if force else row["retry_count"] + 1
    conn.execute(
        "UPDATE cases SET status = 'queued', retry_count = ?, error_detail = NULL,"
        " updated_at = ? WHERE case_id = ?",
        (new_count, utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "operator", "case_retry",
                       {"force": bool(force), "retry_count": new_count})
    return {"ok": True, "status": "queued", "retry_count": new_count}


def rerun_case(conn, case_id: str) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "case not found"}
    if row["status"] != "complete":
        return {"ok": False, "error": "case is not complete"}
    conn.execute(
        "UPDATE cases SET status = 'rerun_requested', updated_at = ? WHERE case_id = ?",
        (utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "operator", "case_rerun_requested",
                       {"run_version": row["run_version"]})
    return {"ok": True, "status": "rerun_requested"}


def delete_case(conn, case_id: str, settings: Settings) -> dict:
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "case not found"}
    held = conn.execute(
        "SELECT 1 FROM legal_holds WHERE case_id=? AND released_at IS NULL",
        (case_id,),
    ).fetchone()
    if held:
        return {"ok": False, "error": "case is on legal hold"}
    # Record intent first; only claim completion after backup scrub and restore
    # proof succeed. audit_events has no FK on case_id, so both entries survive.
    audit.append_event(conn, case_id, "operator", "case_delete_requested",
                       {"verdict": row["verdict"], "status": row["status"],
                        "scope": "database_files_backups_delivery"})
    from uapvf.retention import purge_case
    result = purge_case(conn, settings, case_id, include_backups=True)
    if not result.get("purged"):
        return {"ok": False,
                "error": "case is on legal hold" if result.get("status") == "held"
                else "case purge verification failed",
                "purge": result}
    audit.append_event(conn, case_id, "operator", "case_deleted",
                       {"scope": "database_files_backups_delivery",
                        "restore_proof": result.get("restore_proof")})
    return {"ok": True, "purge": result}


def set_payment(conn, case_id: str, status_value: str) -> dict:
    if status_value not in ("unpaid", "paid", "comped"):
        return {"ok": False, "error": "invalid payment status"}
    row = conn.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
    if row is None:
        return {"ok": False, "error": "case not found"}
    old = row["payment_status"]
    conn.execute(
        "UPDATE cases SET payment_status = ?, updated_at = ? WHERE case_id = ?",
        (status_value, utcnow_iso(), case_id),
    )
    conn.commit()
    audit.append_event(conn, case_id, "operator", "payment_status_changed",
                       {"old": old, "new": status_value})
    return {"ok": True, "old": old, "new": status_value}


# ---------------------------------------------------------------------------
# Background worker pool (§8: 4 threads inside the server process)
# ---------------------------------------------------------------------------

class WorkerPool:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self._stop = threading.Event()
        self._threads: List[threading.Thread] = []

    def start(self) -> None:
        n = max(0, int(self.settings.UAPV_WORKER_THREADS))
        for i in range(n):
            t = threading.Thread(
                target=self._loop, name=f"uapvf-worker-{i}", daemon=True
            )
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)
        self._threads = []

    def _loop(self) -> None:
        from uapvf import db as dbmod

        poll = max(0.05, float(self.settings.UAPV_WORKER_POLL_S))
        while not self._stop.is_set():
            conn = None
            try:
                conn = dbmod.connect(self.settings.db_path)
                release_spend_capped_if_rollover(conn, self.settings)
                row = conn.execute(
                    "SELECT case_id FROM cases WHERE status IN ('queued',"
                    " 'rerun_requested') ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if row:
                    process_one_case(row["case_id"], settings=self.settings,
                                     conn=conn)
                    continue
            except Exception as exc:  # never let a worker die
                log.warning("worker loop error: %s", exc)
            finally:
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
            self._stop.wait(poll)


def drain_queue(settings: Optional[Settings] = None, conn=None) -> List[dict]:
    """Deterministically process every claimable case (used by tests and by
    the benchmark runner's synchronous path)."""
    from uapvf import db as dbmod

    settings = settings or get_settings()
    own_conn = conn is None
    if own_conn:
        conn = dbmod.connect(settings.db_path)
    processed = []
    try:
        release_spend_capped_if_rollover(conn, settings)
        while True:
            row = conn.execute(
                "SELECT case_id FROM cases WHERE status IN ('queued',"
                " 'rerun_requested') ORDER BY created_at ASC LIMIT 1"
            ).fetchone()
            if row is None:
                break
            processed.append(
                process_one_case(row["case_id"], settings=settings, conn=conn)
            )
        return processed
    finally:
        if own_conn:
            conn.close()
