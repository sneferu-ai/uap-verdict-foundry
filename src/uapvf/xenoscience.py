"""Evidence-bounded, seven-stage speculative research synthesis.

Nothing returned by this module is forensic evidence. Inputs are only the
already-persisted verdict/kinematic summaries and operator metadata; the
deterministic fallback remains useful when no external ideation engine is
configured and is explicitly marked as such.
"""
from __future__ import annotations

import hashlib
import json


LABEL = "SPECULATIVE FICTION — NOT FORENSIC EVIDENCE"
STAGES = (
    "observation_boundary",
    "kinematic_hypotheses",
    "energy_constraints",
    "materials_constraints",
    "control_hypotheses",
    "planetary_context",
    "contradiction_review",
)


def build_multi_model_xenoscience(case_id: str, engine_result: dict) -> dict:
    """Wrap a schema-validated Sneferu run with the non-evidence boundary."""
    core = {
        "model_runs": engine_result["model_runs"],
        "stages": engine_result["stages"],
        "consistency_judgment": engine_result["consistency_judgment"],
        "engine_run_id": engine_result.get("engine_run_id") or None,
        "engine_call_ids": engine_result.get("engine_call_ids") or [],
    }
    return {
        "schema_version": 1,
        "case_id": case_id,
        "label": LABEL,
        "speculative": True,
        "evidence_eligible": False,
        "engine": "sneferu-multi-model",
        "single_model_descope": False,
        **core,
        "content_hash": hashlib.sha256(
            json.dumps(core, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def build_xenoscience(case_id: str, verdict: str, metadata: dict,
                      lineage_outputs: list, rigor_result=None) -> dict:
    observed = {
        "verdict": verdict,
        "reported_shape": metadata.get("shape"),
        "reported_duration_seconds": metadata.get("duration_seconds"),
        "lineage_classifications": sorted({
            item.get("classification") for item in lineage_outputs
            if item.get("classification")
        }),
        "rigor_passed": bool((rigor_result or {}).get("passed")),
    }
    seed = int(hashlib.sha256(case_id.encode("utf-8")).hexdigest()[:8], 16)
    candidates = [
        "field-mediated inertial load distribution",
        "distributed electroaerodynamic control",
        "magnetohydrodynamic boundary-layer control",
    ]
    selected = candidates[seed % len(candidates)]
    content = {
        "observation_boundary": {
            "known": observed,
            "unknown": ["range", "true scale", "mass", "propulsion mechanism"],
        },
        "kinematic_hypotheses": {
            "candidate": selected,
            "falsifiers": ["parallax-consistent ordinary motion",
                            "verified aircraft or satellite conjunction"],
        },
        "energy_constraints": {
            "statement": "No energy estimate is possible without range, mass, and velocity.",
            "prohibited_inference": "absence of visible exhaust does not prove reactionless motion",
        },
        "materials_constraints": {
            "requirements": ["thermal cycling tolerance", "low observable glare"],
            "confidence": "conceptual only",
        },
        "control_hypotheses": {
            "requirements": ["closed-loop stabilization", "redundant sensing"],
            "confidence": "conceptual only",
        },
        "planetary_context": {
            "world_seed": f"VF-{seed % 10000:04d}",
            "environmental_constraint": "high-altitude dry atmosphere",
            "derivation": "creative extrapolation, not origin attribution",
        },
        "contradiction_review": {
            "contradictions": [],
            "boundary_passed": True,
            "review_rule": "reject any extraterrestrial-origin statement framed as evidence",
        },
    }
    return {
        "schema_version": 1,
        "case_id": case_id,
        "label": LABEL,
        "speculative": True,
        "evidence_eligible": False,
        "engine": "deterministic-single-model-fallback",
        "single_model_descope": True,
        "stages": [{"stage": name, "output": content[name]} for name in STAGES],
        "content_hash": hashlib.sha256(
            json.dumps(content, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    }


def validate_boundary(output: dict) -> list:
    errors = []
    if output.get("label") != LABEL or output.get("evidence_eligible") is not False:
        errors.append("non-evidence boundary metadata missing")
    names = [item.get("stage") for item in output.get("stages") or []]
    if names != list(STAGES):
        errors.append("xenoscience stage sequence incomplete")
    if output.get("single_model_descope") is False:
        model_runs = output.get("model_runs") or []
        distinct = {item.get("model_id") for item in model_runs
                    if isinstance(item, dict) and item.get("model_id")}
        if len(distinct) < 2:
            errors.append("multi-model xenoscience has fewer than two models")
        if not isinstance(output.get("consistency_judgment"), dict):
            errors.append("multi-model consistency judgment missing")
    serialized = json.dumps(output).lower()
    prohibited = ("confirmed extraterrestrial", "proved alien", "evidence of aliens")
    if any(term in serialized for term in prohibited):
        errors.append("prohibited origin assertion")
    return errors
