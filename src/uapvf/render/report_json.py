"""report.json machine record (spec §5 schema, FR-009)."""
from __future__ import annotations

import json
from typing import List, Optional

from uapvf import __version__
from uapvf.config import EXPECTED_LINEAGE_COUNT, utcnow_iso


def build_report_dict(
    case,
    battery_results: List[dict],
    lineage_outputs: List[dict],
    uncertainty: dict,
    warnings: List[dict],
    phase2: Optional[dict] = None,
) -> dict:
    phase2 = phase2 or {}
    report = {
        "case_id": case["case_id"],
        "verdict": case["verdict"],
        "verdict_text": case["verdict_text"],
        "uncertainty": {
            "value": uncertainty.get("value"),
            "calibrated": bool(uncertainty.get("calibrated")),
            "calibration_source": uncertainty.get("calibration_source"),
            "low_confidence": bool(uncertainty.get("low_confidence")),
            "uncertainty_reason": uncertainty.get("uncertainty_reason"),
            "raw_confidence": uncertainty.get("raw_confidence"),
            "penalty": uncertainty.get("penalty"),
        },
        "battery_results": [
            {
                "category": r["category"],
                "result": r["result"],
                "evidence_citation": r.get("evidence_citation"),
                "source_stamp": r.get("source_stamp"),
            }
            for r in battery_results
        ],
        "lineage_outputs": [
            {
                "lineage_id": o.get("lineage_id"),
                "classification": o.get("classification"),
                "artifact_detected": bool(o.get("artifact_detected")),
                "hypothesis": o.get("hypothesis"),
                "status": o.get("status") or "unknown",
                "confidence": o.get("confidence"),
                "evidence_claims": o.get("evidence_claims") or [],
                "provenance": o.get("provenance") or {},
                "abstention_reason": o.get("abstention_reason"),
            }
            for o in lineage_outputs
        ],
        "agreement_fraction": case["agreement_fraction"],
        "quality_score": case["quality_score"],
        "quality_gate_pass": bool(case["quality_gate_pass"]),
        "warnings": warnings or [],
        "provenance": {
            "media_sha256": case["media_sha256"],
            "observed_at": case["observed_at"],
            "latitude": case["latitude"],
            "longitude": case["longitude"],
        },
        "generated_at": utcnow_iso(),
        "software_version": __version__,
    }
    report["analysis_contract"] = {
        "lineage_runtime": phase2.get("lineage_runtime") or "unknown",
        "expected_lineages": EXPECTED_LINEAGE_COUNT,
        "observed_lineages": len(lineage_outputs),
        "sandbox_verified": bool(phase2.get("sandbox_verified")),
        "coverage": phase2.get("coverage") or {},
        "rigor": phase2.get("rigor") or {},
        "reference_integrity": phase2.get("references") or {},
    }
    report["evidence_section"] = {
        "battery_result_count": len(battery_results),
        "lineage_result_count": len(lineage_outputs),
        "evidence_eligible": True,
    }
    return report


def serialize_report_json(report: dict) -> str:
    return json.dumps(report, sort_keys=True, ensure_ascii=False, indent=1)
