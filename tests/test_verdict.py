"""Verdict rule + uncertainty calibration (AC-004, AC-006, AC-015,
FR-007/FR-008)."""
from __future__ import annotations

import json

from conftest import base_fields

from uapvf.verdict import (
    VERDICT_INSUFFICIENT,
    VERDICT_MUNDANE,
    VERDICT_NO_MUNDANE,
    compute_uncertainty,
    synthesize_verdict,
)


def res(cat, result, cite="some evidence"):
    return {"category": cat, "result": result, "evidence_citation": cite,
            "source_stamp": {}}


class TestVerdictRule:
    def test_empty_battery_is_insufficient(self):
        verdict = synthesize_verdict([], [])
        assert verdict["verdict"] == VERDICT_INSUFFICIENT
        assert verdict["reason"] == "empty_battery"

    def test_any_positive_is_mundane(self):
        results = [res("aircraft", "positive"), res("satellites", "negative"),
                   res("lens_artifacts", "negative"),
                   res("astronomical", "negative")]
        v = synthesize_verdict(results, [])
        assert v["verdict"] == VERDICT_MUNDANE
        assert "aircraft" in v["verdict_text"]

    def test_positive_beats_insufficient(self):
        results = [res("aircraft", "positive"), res("satellites", "insufficient")]
        assert synthesize_verdict(results, [])["verdict"] == VERDICT_MUNDANE

    def test_positive_citation_has_one_terminal_period(self):
        result = synthesize_verdict(
            [res("drones", "positive", "Two lineages matched.")], []
        )
        assert result["verdict_text"].endswith("matched.")
        assert not result["verdict_text"].endswith("matched..")

    def test_no_positive_with_insufficient(self):
        results = [res("aircraft", "negative"), res("satellites", "insufficient")]
        assert synthesize_verdict(results, [])["verdict"] == VERDICT_INSUFFICIENT

    def test_all_negative_is_no_mundane_match(self):
        results = [res("aircraft", "negative"), res("satellites", "negative"),
                   res("lens_artifacts", "negative"),
                   res("astronomical", "negative")]
        assert synthesize_verdict(results, [])["verdict"] == VERDICT_NO_MUNDANE

    def test_drone_classification_forces_insufficient(self):
        results = [res("aircraft", "negative"), res("satellites", "negative")]
        lineages = [{"classification": "drone", "lineage_id": "l1",
                     "artifact_detected": False}]
        v = synthesize_verdict(results, lineages)
        assert v["verdict"] == VERDICT_INSUFFICIENT
        assert "drone" in v["verdict_text"]

    def test_unknown_classification_no_effect(self):
        results = [res("aircraft", "negative"), res("satellites", "negative")]
        lineages = [{"classification": "unknown", "lineage_id": "l1",
                     "artifact_detected": False}]
        assert synthesize_verdict(results, lineages)["verdict"] == VERDICT_NO_MUNDANE

    def test_no_extraterrestrial_claims_language(self):
        results = [res("aircraft", "negative")]
        for verdict_fn in (
            lambda: synthesize_verdict([res("aircraft", "positive")], []),
            lambda: synthesize_verdict(results, []),
            lambda: synthesize_verdict([res("aircraft", "insufficient")], []),
        ):
            v = verdict_fn()
            text = v["verdict_text"].lower()
            assert "extraterrestrial" not in text or "not" in text
            assert "alien" not in text
            assert "non-human" not in text


class TestQualifications:
    """Spec §3.4/§3.5: no_mundane_match carries explicit qualifications
    (astronomical OBL-10 note, coverage sparsity, fail-closed rigor)."""

    def test_astronomical_qualification_on_nmm(self):
        results = [res("aircraft", "negative"), res("astronomical", "negative")]
        lineages = [{"classification": "astronomical",
                     "lineage_id": "l1-photometric", "artifact_detected": False}]
        v = synthesize_verdict(results, lineages)
        assert v["verdict"] == VERDICT_NO_MUNDANE
        assert v["astronomical_classification_present"] is True
        assert "astronomical battery adapter checked" in v["verdict_text"]
        assert "astronomical_classification_present" in v["qualifications"]

    def test_astronomical_positive_is_mundane_no_qualification(self):
        results = [res("astronomical", "positive")]
        lineages = [{"classification": "astronomical",
                     "lineage_id": "l1-photometric", "artifact_detected": False}]
        v = synthesize_verdict(results, lineages)
        assert v["verdict"] == VERDICT_MUNDANE
        assert v["astronomical_classification_present"] is False
        assert v["qualifications"] == []

    def test_rigor_not_certified_qualifies_nmm(self):
        results = [res("aircraft", "negative")]
        rigor_result = {"passed": False, "reason": "conditions_failed:4,5",
                        "runtime": "live"}
        v = synthesize_verdict(results, [], rigor_result=rigor_result)
        assert v["verdict"] == VERDICT_NO_MUNDANE
        assert "rigor_not_certified" in v["qualifications"]
        assert "provisional" in v["verdict_text"]
        assert v["rigor_passed"] is False

    def test_rigor_passed_no_provisional(self):
        results = [res("aircraft", "negative")]
        rigor_result = {"passed": True, "reason": None, "runtime": "live"}
        v = synthesize_verdict(results, [], rigor_result=rigor_result)
        assert v["verdict"] == VERDICT_NO_MUNDANE
        assert "rigor_not_certified" not in v["qualifications"]
        assert v["rigor_passed"] is True

    def test_coverage_sparse_qualifies_nmm(self):
        results = [res("aircraft", "negative")]
        v = synthesize_verdict(results, [], coverage={"weather": "sparse"})
        assert v["verdict"] == VERDICT_NO_MUNDANE
        assert "coverage_sparse" in v["qualifications"]
        assert "coverage is sparse" in v["verdict_text"]

    def test_no_qualification_on_mundane(self):
        results = [res("aircraft", "positive")]
        rigor_result = {"passed": False, "reason": "x", "runtime": "live"}
        v = synthesize_verdict(results, [], rigor_result=rigor_result,
                               coverage={"weather": "sparse"})
        assert v["verdict"] == VERDICT_MUNDANE
        assert v["qualifications"] == []


class TestUncertainty:
    def make_settings(self, tmp_var):
        from uapvf.config import get_settings

        return get_settings()

    def test_no_calibration_returns_none(self, settings):
        results = [res("aircraft", "negative")]
        u = compute_uncertainty(VERDICT_NO_MUNDANE, results, 1.0, 3, 0.9,
                                settings)
        assert u["calibrated"] is False
        assert u["value"] is None
        assert u["uncertainty_reason"] == "no calibration run available"

    def test_zero_or_one_lineage_penalty(self, settings):
        results = [res("aircraft", "negative")]
        u3 = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.0, 3, 0.5,
                                 settings)
        u1 = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.0, 1, 0.5,
                                 settings)
        # raw_confidence is reported pre-penalty; the penalty field carries it
        assert u3["penalty"] == 0.0
        assert u1["penalty"] == 0.15
        assert u3["raw_confidence"] == u1["raw_confidence"]
        u0 = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.0, 0, 0.5,
                                 settings)
        assert u0["penalty"] == 0.15

    def test_low_confidence_flag(self, settings):
        results = [res("aircraft", "negative")]
        u = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.34, 3, 0.5,
                                settings)
        assert u["low_confidence"] is True
        u2 = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.67, 3, 0.5,
                                 settings)
        assert u2["low_confidence"] is False
        # single lineage: no disagreement to flag even at low agreement
        u1 = compute_uncertainty(VERDICT_NO_MUNDANE, results, 0.0, 1, 0.5,
                                 settings)
        assert u1["low_confidence"] is False

    def test_calibration_applies_bin_probability(self, settings):
        calib_dir = settings.var_dir / "benchmark"
        calib_dir.mkdir(parents=True, exist_ok=True)
        bins = []
        for i in range(10):
            bins.append({
                "index": i, "lower_bound": i / 10, "upper_bound": (i + 1) / 10,
                "upper_inclusive": i == 9, "total_cases": 1, "correct": 1,
                "probability": 0.1 * (i + 1),
            })
        (calib_dir / "calibration.json").write_text(json.dumps({
            "mode": "live", "validation_set_id": "vs-1",
            "validation_set_hash": "h", "bins": bins,
        }))
        results = [res("aircraft", "positive", cite="evidence")]
        u = compute_uncertainty(VERDICT_MUNDANE, results, 1.0, 3, 1.0,
                                settings)
        assert u["calibrated"] is True
        assert u["value"] is not None
        assert u["calibration_source"]["validation_set_id"] == "vs-1"

    def test_mock_mode_calibration_not_used(self, settings, monkeypatch):
        monkeypatch.setenv("SNEFERU_MOCK", "1")
        calib_dir = settings.var_dir / "benchmark"
        calib_dir.mkdir(parents=True, exist_ok=True)
        (calib_dir / "calibration.json").write_text(json.dumps({
            "mode": "mock", "validation_set_id": "vs-mock",
            "validation_set_hash": "h",
            "bins": [{"index": 9, "lower_bound": 0.9, "upper_bound": 1.0,
                      "upper_inclusive": True, "total_cases": 1, "correct": 1,
                      "probability": 0.9}],
        }))
        results = [res("aircraft", "positive", cite="evidence")]
        u = compute_uncertainty(VERDICT_MUNDANE, results, 1.0, 3, 1.0,
                                settings)
        assert u["calibrated"] is False
