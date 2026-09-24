"""Rigor engine tests (spec §3.3): classification-gated refutation-consistent
concordance with positive evidence overlap, population variance, structured
refutation with defense resolutions, six rigor conditions per runtime, null
semantics, and final cross-modal finalization.
"""
from __future__ import annotations

import statistics

import pytest

from uapvf.lineages import EvidenceClaims, LineageOutput
from uapvf.rigor import (
    ASTRONOMICAL_QUALIFICATION_NOTE,
    compute_concordance,
    adjudicate_conflicts,
    finalize_rigor,
    load_refutation_rules,
    load_rigor_config,
    passes_agreement_thresholds,
    population_variance,
    run_refutation,
)


def mk(
    lineage_id,
    classification,
    shared,
    specific,
    defense=(),
    status="ok",
    confidence=0.9,
):
    return LineageOutput(
        lineage_id=lineage_id,
        status=status,
        classification=classification,
        evidence_claims=EvidenceClaims(shared=list(shared),
                                       specific=list(specific)),
        defense_claims=list(defense),
        confidence=confidence,
        rationale="fixture",
    )


# The unanimous-aircraft fixture. Shared-claim sets are chosen so every pair
# has positive evidence overlap except the eight disjoint pairs scoring 0.5
# (computed in test_unanimous_*). No structural conflicts, no
# claim-classification inconsistencies with "aircraft".
UNANIMOUS = [
    mk("l1-photometric", "aircraft",
       ["point_source", "anomalous_signature", "luminous"],
       ["luminance_profile_consistent_with_known_object",
        "high_luminance_ratio"]),
    mk("l2-spectral", "aircraft",
       ["luminous", "anomalous_signature", "frequency_signature"],
       ["spectral_entropy_anomalous", "frequency_harmonic_pattern"]),
    mk("l3-geometric", "aircraft",
       ["point_source", "motion_detected", "sharp_boundary"],
       ["geometric_contour_matched", "shape_symmetry_detected"]),
    mk("l4-metadata", "aircraft",
       ["anomalous_signature", "metadata_anomaly", "temporal_consistency"],
       ["exif_device_identified", "timestamp_metadata_consistent"]),
    mk("l5-trajectory", "aircraft",
       ["motion_detected", "periodic_behavior", "temporal_consistency"],
       ["steady_trajectory_detected", "hover_behavior_detected"]),
    mk("l6-resnet50", "aircraft",
       ["luminous", "sharp_boundary", "anomalous_signature"],
       ["cnn_classification_confident", "cnn_feature_pattern_matched"]),
    mk("l7-audio", "aircraft",
       ["motion_detected", "periodic_behavior", "frequency_signature"],
       ["audio_spectral_signature_detected",
        "audio_rotor_signature_detected"]),
]

# 6/1 split: l7 dissents with sensor_artifact (no claim in the ruleset is
# inconsistent with sensor_artifact, so no cross challenges arise and the
# variance arithmetic isolates the split pattern).
DISSENTER = mk("l7-audio", "sensor_artifact",
               ["motion_detected", "periodic_behavior", "frequency_signature"],
               ["audio_spectral_signature_detected",
                "audio_environmental_match"])


def unanimous():
    return [LineageOutput.from_dict(o.to_dict()) for o in UNANIMOUS]


def six_one_split():
    outs = unanimous()
    outs[-1] = DISSENTER
    return outs


# Round 4 removed the ``simulation`` runtime (spec's two-mode model: live
# engine vs mock fixtures). Metric tests that used the relaxed simulation
# gate set now run CERTIFIED live — runtime ``live`` with operator-validated
# independence + calibration — which passes on the same fixtures and keeps
# every condition assertion meaningful.
CERTIFIED_LIVE = dict(
    runtime="live", independence_validated=True, calibration_valid=True
)


# ---------------------------------------------------------------------------
# Config + rules
# ---------------------------------------------------------------------------


def test_rigor_config_matches_spec():
    cfg = load_rigor_config()
    assert cfg["agreement_metric"] == (
        "classification_gated_refutation_consistent_concordance_"
        "with_evidence_overlap"
    )
    assert cfg["agreement_fraction_threshold"] == 0.5
    assert cfg["variance_statistic"] == "population_variance"
    assert cfg["variance_threshold"] == 0.15
    assert cfg["threshold_unit"] == "absolute"
    assert cfg["min_plurality_count"] == 4
    assert cfg["calibration_accuracy_threshold"] == 0.6
    assert cfg["calibration_min_comparable"] == 80
    # Round 4: exactly two modes — live (all six conditions) and blocked
    # mock. The removed ``simulation`` runtime must stay absent.
    assert cfg["rigor_conditions"] == {
        "live": [1, 2, 3, 4, 5, 6],
        "mock": "blocked",
    }


def test_refutation_rules_complete():
    rules = load_refutation_rules()
    assert rules["version"] == "1.1.0"
    assert len(rules["claim_classification_inconsistencies"]) == 20
    assert len(rules["structural_inconsistencies"]) == 3
    assert len(rules["specific_claim_contradictions"]) == 4
    assert len(rules["defense_resolutions"]) == 5
    # OBL-3: file_structure_anomaly lists 8 explicit categories, no sentinel.
    fsa = next(
        r for r in rules["claim_classification_inconsistencies"]
        if r["claim"] == "file_structure_anomaly"
    )
    assert len(fsa["inconsistent_with"]) == 8
    assert "sensor_artifact" not in fsa["inconsistent_with"]
    assert "lens_artifact" not in fsa["inconsistent_with"]


def test_threshold_boundary_semantics():
    # agreement_fraction >= 0.5 (0.5 passes, 0.4999 blocks)
    assert passes_agreement_thresholds(0.5, 0.0) is True
    assert passes_agreement_thresholds(0.4999, 0.0) is False
    # variance < 0.15 (0.15 blocks)
    assert passes_agreement_thresholds(0.9, 0.149) is True
    assert passes_agreement_thresholds(0.9, 0.15) is False


def test_population_variance_is_stddev_squared():
    values = [0.8333, 0.8333, 0.8333, 0.8333, 0.8333, 0.8333, 0.0]
    assert population_variance(values) == pytest.approx(
        statistics.pvariance(values)
    )
    assert population_variance([]) == 0.0


# ---------------------------------------------------------------------------
# Concordance metric
# ---------------------------------------------------------------------------


def test_unanimous_certified_live_passes():
    result = compute_concordance(unanimous(), **CERTIFIED_LIVE)
    # 13 full-agreement pairs + 8 disjoint-claim pairs at 0.5.
    assert result.agreement_fraction == pytest.approx(17.0 / 21.0)
    assert result.pair_count == 21
    expected_c = {
        "l1-photometric": 5 / 6,
        "l2-spectral": 5 / 6,
        "l3-geometric": 5 / 6,
        "l4-metadata": 5 / 6,
        "l5-trajectory": 0.75,
        "l6-resnet50": 5 / 6,
        "l7-audio": 0.75,
    }
    for lid, value in expected_c.items():
        assert result.per_lineage_concordance[lid] == pytest.approx(value)
    assert result.variance == pytest.approx(
        statistics.pvariance(list(expected_c.values()))
    )
    assert result.variance < 0.15
    assert result.plurality == "aircraft"
    assert result.plurality_count == 7
    assert result.mean_pair_confidence == pytest.approx(0.9)
    assert result.mean_pair_confidence_note is None
    assert result.conditions[1] is True
    assert result.conditions[2] is True
    assert result.conditions[3] is True
    assert result.conditions[6] is True
    assert result.conditions[4] is True  # operator-validated gates supplied
    assert result.conditions[5] is True
    assert result.required_conditions == [1, 2, 3, 4, 5, 6]
    assert result.passed is True
    assert result.reason is None


def test_removed_simulation_runtime_is_blocked():
    """Round 4: the ``simulation`` runtime was removed from the spec's
    two-mode model. The shipped ``rigor_conditions`` table no longer names
    it, so any call presenting it is blocked fail-closed — it can never
    select a relaxed gate set again."""
    result = compute_concordance(unanimous(), runtime="simulation")
    assert result.passed is False
    assert result.reason == "runtime_blocked"
    assert result.required_conditions == "blocked"


def test_unanimous_live_fail_closed_without_operator_eval_sets():
    result = compute_concordance(unanimous(), runtime="live")
    assert result.passed is False
    assert "4" in result.reason and "5" in result.reason
    result2 = compute_concordance(
        unanimous(),
        runtime="live",
        independence_validated=True,
        calibration_valid=True,
    )
    assert result2.passed is True  # ALRC path attainable when validated


def test_mock_runtime_always_blocked():
    result = compute_concordance(unanimous(), runtime="mock")
    assert result.passed is False
    assert result.reason == "runtime_blocked"
    assert result.required_conditions == "blocked"


def test_six_one_split_still_passes_condition_one():
    """OBL-102: a 6/1 split has population variance < 0.15 and agreement
    >= 0.5, so one honest dissenter does not block rigor."""
    result = compute_concordance(six_one_split(), **CERTIFIED_LIVE)
    assert result.agreement_fraction == pytest.approx(12.5 / 21.0)
    expected_c = [0.75, 4 / 6, 4 / 6, 0.75, 3.5 / 6, 0.75, 0.0]
    assert result.variance == pytest.approx(statistics.pvariance(expected_c))
    assert result.variance < 0.15
    assert result.conditions[1] is True
    assert result.plurality == "aircraft"
    assert result.plurality_count == 6
    assert result.conditions[2] is True
    assert result.refutation.passed is True
    assert result.passed is True


def test_disjoint_claims_same_classification_score_half():
    """DIS-2/OBL-63: same classification with disjoint evidence claims is
    partial agreement (0.5), not full agreement."""
    outs = [
        mk("l1-photometric", "aircraft",
           ["point_source", "anomalous_signature", "luminous"],
           ["luminance_profile_consistent_with_known_object",
            "high_luminance_ratio"]),
        mk("l5-trajectory", "aircraft",
           ["motion_detected", "periodic_behavior", "temporal_consistency"],
           ["steady_trajectory_detected", "hover_behavior_detected"]),
    ]
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.pair_agreements["l1-photometric:l5-trajectory"] == 0.5
    assert result.agreement_fraction == pytest.approx(0.5)
    assert result.conditions[1] is True  # 0.5 passes the >= threshold


def test_overlapping_claims_same_classification_score_one():
    outs = [
        mk("l1-photometric", "aircraft",
           ["point_source", "anomalous_signature", "luminous"],
           ["luminance_profile_consistent_with_known_object",
            "high_luminance_ratio"]),
        mk("l2-spectral", "aircraft",
           ["luminous", "anomalous_signature", "frequency_signature"],
           ["spectral_entropy_anomalous", "frequency_harmonic_pattern"]),
    ]
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.pair_agreements["l1-photometric:l2-spectral"] == 1.0


def test_different_classifications_score_zero():
    outs = [
        mk("l1-photometric", "aircraft",
           ["point_source", "anomalous_signature", "luminous"],
           ["luminance_profile_consistent_with_known_object",
            "high_luminance_ratio"]),
        mk("l2-spectral", "satellite",
           ["luminous", "anomalous_signature", "frequency_signature"],
           ["spectral_entropy_anomalous", "frequency_harmonic_pattern"]),
    ]
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.pair_agreements["l1-photometric:l2-spectral"] == 0.0
    assert result.mean_pair_confidence is None
    assert result.mean_pair_confidence_note == "no_agreeing_pairs"


def test_null_classification_scores_zero_but_comparable():
    """OBL-13: classification null, status ok -> pair_agreement 0.0, but the
    lineage still counts toward all-lineages-comparable."""
    outs = six_one_split()
    outs[-1] = mk(
        "l7-audio", None,
        ["motion_detected", "periodic_behavior", "frequency_signature"],
        ["audio_spectral_signature_detected", "audio_environmental_match"],
    )
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    for key, score in result.pair_agreements.items():
        if key.endswith("l7-audio"):
            assert score == 0.0
    assert result.conditions[3] is True  # all 7 status ok
    assert result.plurality_count == 6  # nulls excluded from plurality
    assert result.agreement_fraction == pytest.approx(12.5 / 21.0)


def test_mean_pair_confidence_is_min_over_agreeing_pairs():
    outs = [
        mk("l1-photometric", "aircraft",
           ["point_source", "anomalous_signature", "luminous"],
           ["luminance_profile_consistent_with_known_object",
            "high_luminance_ratio"],
           confidence=0.4),
        mk("l2-spectral", "aircraft",
           ["luminous", "anomalous_signature", "frequency_signature"],
           ["spectral_entropy_anomalous", "frequency_harmonic_pattern"],
           confidence=0.8),
    ]
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.mean_pair_confidence == pytest.approx(0.4)


def test_plurality_tie_is_deterministic_and_below_threshold():
    outs = unanimous()
    aircraft = {"l1-photometric", "l2-spectral", "l6-resnet50"}
    for i, out in enumerate(outs):
        if out.lineage_id in aircraft:
            continue
        if out.lineage_id == "l5-trajectory":
            outs[i] = mk(out.lineage_id, None,
                         list(out.evidence_claims.shared),
                         list(out.evidence_claims.specific))
        else:
            outs[i] = mk(out.lineage_id, "satellite",
                         list(out.evidence_claims.shared),
                         list(out.evidence_claims.specific))
    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.plurality_count == 3
    assert result.plurality == "aircraft"  # alphabetical tie-break
    assert result.conditions[2] is False
    assert result.passed is False


# ---------------------------------------------------------------------------
# Refutation engine
# ---------------------------------------------------------------------------


def test_within_lineage_structural_contradiction_refutes():
    outs = [
        mk("l3-geometric", "aircraft",
           ["point_source", "extended_source", "motion_detected"],
           ["geometric_contour_matched", "shape_symmetry_detected"]),
    ]
    refutation = run_refutation(outs)
    assert "l3-geometric" in refutation.refuted
    assert refutation.passed is False
    assert any(c.challenge_type == "within_lineage_contradiction"
               for c in refutation.challenges)

    result = compute_concordance(outs, **CERTIFIED_LIVE)
    assert result.conditions[6] is False
    assert result.conditions[3] is False  # refuted -> non_comparable (§3.2)


def test_specific_claim_contradictions_refute():
    # l5: ballistic + hover (and ballistic + steady) cannot co-occur.
    l5_bad = mk("l5-trajectory", "meteor",
                ["motion_detected", "periodic_behavior", "temporal_consistency"],
                ["ballistic_trajectory_detected", "hover_behavior_detected"])
    assert "l5-trajectory" in run_refutation([l5_bad]).refuted

    # l7: rotor signature is not environmental.
    l7_bad = mk("l7-audio", "drone",
                ["motion_detected", "periodic_behavior", "frequency_signature"],
                ["audio_rotor_signature_detected", "audio_environmental_match"])
    assert "l7-audio" in run_refutation([l7_bad]).refuted

    # l4: identified device implies normal file structure.
    l4_bad = mk("l4-metadata", "sensor_artifact",
                ["anomalous_signature", "metadata_anomaly",
                 "temporal_consistency"],
                ["exif_device_identified", "file_structure_anomaly"])
    assert "l4-metadata" in run_refutation([l4_bad]).refuted


def test_cross_challenge_defended_by_velocity_profile():
    """OBL-90 defense resolution #1: l5's hover claim is challenged by l1's
    meteor classification; l5 defends with velocity_profile (emitted)."""
    l5 = mk("l5-trajectory", "drone",
            ["motion_detected", "periodic_behavior", "velocity_profile"],
            ["hover_behavior_detected", "steady_trajectory_detected"],
            defense=["velocity_profile"])
    l1 = mk("l1-photometric", "meteor",
            ["point_source", "luminous", "anomalous_signature"],
            ["luminance_gradient_anomalous",
             "luminance_profile_consistent_with_known_object"])
    refutation = run_refutation([l5, l1])
    challenges = [c for c in refutation.challenges
                  if c.challenge_type == "cross_classification_inconsistency"]
    assert len(challenges) == 1
    challenge = challenges[0]
    assert challenge.challenged_lineage == "l5-trajectory"
    assert challenge.challenging_lineage == "l1-photometric"
    assert challenge.claim == "hover_behavior_detected"
    assert challenge.inconsistent_with == "meteor"
    assert challenge.resolved is True
    assert challenge.defense_claim == "velocity_profile"
    assert refutation.passed is True


def test_cross_challenge_without_defense_refutes():
    l5 = mk("l5-trajectory", "drone",
            ["motion_detected", "periodic_behavior", "velocity_profile"],
            ["hover_behavior_detected", "steady_trajectory_detected"])
    l1 = mk("l1-photometric", "meteor",
            ["point_source", "luminous", "anomalous_signature"],
            ["luminance_gradient_anomalous",
             "luminance_profile_consistent_with_known_object"])
    refutation = run_refutation([l5, l1])
    assert refutation.refuted == ["l5-trajectory"]

    result = compute_concordance([l5, l1], **CERTIFIED_LIVE)
    assert result.conditions[6] is False
    assert result.passed is False


def test_defense_claim_must_be_emitted():
    """Resolution #3: rotor audio vs a bird challenger is defensible only
    with frequency_signature — and only if l7 actually emitted it. A defense
    claim that is in the lineage's pool but not in its emitted claims cannot
    defend."""
    l7 = mk("l7-audio", "drone",
            ["motion_detected", "periodic_behavior", "velocity_profile"],
            ["audio_rotor_signature_detected",
             "audio_spectral_signature_detected"],
            defense=["frequency_signature"])
    l1 = mk("l1-photometric", "bird",
            ["point_source", "luminous", "anomalous_signature"],
            ["luminance_profile_consistent_with_known_object",
             "high_luminance_ratio"])
    # rotor is inconsistent with bird -> challenge against l7.
    refutation = run_refutation([l7, l1])
    challenges = [c for c in refutation.challenges
                  if c.challenge_type == "cross_classification_inconsistency"]
    assert len(challenges) == 1
    assert challenges[0].challenged_lineage == "l7-audio"
    assert "l7-audio" in refutation.refuted  # not emitted -> no defense

    # Same scenario, but l7 emits frequency_signature -> resolved.
    l7_emitted = mk("l7-audio", "drone",
                    ["motion_detected", "periodic_behavior",
                     "frequency_signature"],
                    ["audio_rotor_signature_detected",
                     "audio_spectral_signature_detected"],
                    defense=["frequency_signature"])
    refutation2 = run_refutation([l7_emitted, l1])
    assert refutation2.passed is True
    challenge2 = refutation2.challenges[0]
    assert challenge2.resolved is True
    assert challenge2.defense_claim == "frequency_signature"


def test_self_classification_inconsistency_has_no_defense():
    """OBL-20: a claim inconsistent with the lineage's OWN classification is
    a self-inconsistency; no defense is possible."""
    l5 = mk("l5-trajectory", "bird",
            ["motion_detected", "periodic_behavior", "temporal_consistency"],
            ["ballistic_trajectory_detected", "hover_behavior_detected"],
            defense=["temporal_consistency"])
    l7 = mk("l7-audio", "bird",
            ["motion_detected", "periodic_behavior", "frequency_signature"],
            ["audio_spectral_signature_detected",
             "audio_environmental_match"])
    refutation = run_refutation([l5, l7])
    self_challenges = [c for c in refutation.challenges
                       if c.challenge_type == "self_classification_inconsistency"]
    assert self_challenges
    challenge = next(c for c in self_challenges
                     if c.challenged_lineage == "l5-trajectory")
    assert challenge.claim == "ballistic_trajectory_detected"
    assert challenge.inconsistent_with == "bird"
    assert challenge.resolved is False
    assert "l5-trajectory" in refutation.refuted
    assert "l7-audio" not in refutation.refuted


def test_structural_conflict_between_pair_refutes_both():
    l1 = mk("l1-photometric", "aircraft",
            ["point_source", "anomalous_signature", "luminous"],
            ["luminance_profile_consistent_with_known_object",
             "high_luminance_ratio"])
    l2 = mk("l2-spectral", "aircraft",
            ["extended_source", "anomalous_signature", "frequency_signature"],
            ["spectral_entropy_anomalous", "frequency_harmonic_pattern"])
    refutation = run_refutation([l1, l2])
    assert set(refutation.refuted) == {"l1-photometric", "l2-spectral"}
    result = compute_concordance([l1, l2], **CERTIFIED_LIVE)
    assert result.pair_agreements["l1-photometric:l2-spectral"] == 0.0
    assert result.conditions[6] is False


def test_refutation_report_serializes():
    report = run_refutation(unanimous())
    data = report.to_dict()
    assert data["passed"] is True
    assert data["refuted"] == []
    assert data["challenges"] == []


# ---------------------------------------------------------------------------
# Adjudication trace + final rigor
# ---------------------------------------------------------------------------


def test_adjudicate_conflicts_trace_for_dissenter():
    outs = six_one_split()
    trace = adjudicate_conflicts(outs, consensus="aircraft")
    assert trace.consensus == "aircraft"
    assert trace.consensus_count == 6
    assert len(trace.entries) == 1
    entry = trace.entries[0]
    assert entry["lineage_id"] == "l7-audio"
    assert entry["classification"] == "sensor_artifact"
    assert entry["disposition"] == "retained_dissenting"
    assert all(score == 0.0
               for score in entry["pair_agreements"].values())


def test_adjudicate_conflicts_marks_refuted_excluded():
    l5 = mk("l5-trajectory", "drone",
            ["motion_detected", "periodic_behavior", "velocity_profile"],
            ["hover_behavior_detected", "steady_trajectory_detected"])
    l1 = mk("l1-photometric", "meteor",
            ["point_source", "luminous", "anomalous_signature"],
            ["luminance_gradient_anomalous",
             "luminance_profile_consistent_with_known_object"])
    trace = adjudicate_conflicts([l5, l1], consensus="meteor")
    entry = next(e for e in trace.entries if e["lineage_id"] == "l5-trajectory")
    assert entry["disposition"] == "excluded_refuted"


def test_finalize_rigor_cross_modal_conflict_blocks():
    preliminary = compute_concordance(unanimous(), **CERTIFIED_LIVE)
    assert preliminary.passed is True
    final = finalize_rigor(
        preliminary,
        battery_verdict="mundane_identified",
        lineage_plurality="drone",
        battery_category="aircraft",
    )
    assert final.passed is False
    assert final.cross_modal_conflict is True
    assert "cross_modal_conflict" in final.reason
    assert final.stage == "final"


def test_finalize_rigor_astronomical_qualification():
    preliminary = compute_concordance(unanimous(), **CERTIFIED_LIVE)
    final = finalize_rigor(
        preliminary,
        battery_verdict="no_mundane_match_astronomical_checked",
        lineage_plurality="astronomical",
    )
    assert final.cross_modal_conflict is False
    assert final.passed is preliminary.passed
    assert final.note == ASTRONOMICAL_QUALIFICATION_NOTE


def test_finalize_rigor_preserves_preliminary_failure():
    preliminary = compute_concordance(unanimous(), runtime="live")
    assert preliminary.passed is False
    final = finalize_rigor(
        preliminary,
        battery_verdict="no_mundane_match",
        lineage_plurality=preliminary.plurality,
    )
    assert final.passed is False
    assert final.reason == preliminary.reason
    assert final.cross_modal_conflict is False


def test_finalize_rigor_no_conflict_when_battery_silent():
    preliminary = compute_concordance(unanimous(), **CERTIFIED_LIVE)
    final = finalize_rigor(
        preliminary,
        battery_verdict="no_mundane_match",
        lineage_plurality="aircraft",
    )
    assert final.passed is True
    assert final.note is None


def test_rigor_result_serializes():
    result = compute_concordance(unanimous(), **CERTIFIED_LIVE)
    data = result.to_dict()
    assert data["passed"] is True
    assert data["pair_count"] == 21
    assert data["conditions"]["1"] is True
    assert data["refutation"]["passed"] is True
    assert data["variance"] == pytest.approx(result.variance)
    assert len(data["pair_agreements"]) == 21


# ---------------------------------------------------------------------------
# Operator certification gates: conditions 4/5 wired to stored artifacts
# (spec §3.2 pt 8, §3.3 condition table; round-3 reviewer note: validate
# the stored report's protocol parameters against rigor_config.json,
# never trust the file)
# ---------------------------------------------------------------------------

import json  # noqa: E402
import hashlib  # noqa: E402
from itertools import combinations  # noqa: E402

from uapvf.lineages.registry import declared_lineage_ids  # noqa: E402
from uapvf.rigor import (  # noqa: E402
    CALIBRATION_DIR_NAME,
    EXPECTED_PAIR_COUNT,
    INDEPENDENCE_REPORT_NAME,
    evaluate_operator_gates,
    validate_stored_calibration,
    validate_stored_independence_report,
)

GATE_IDS = declared_lineage_ids()

RECORDER_PROVENANCE = {
    "recorder": "uapvf_seven_lineage_sandbox",
    "labels_blinded_during_inference": True,
    "collection_manifest_sha256": "a" * 64,
}


def _independence_report(**overrides):
    """A spec-shaped operator independence report (all 21 pairs pass at
    protocol parameters) — the document `lineages validate` stores."""
    pairs = []
    for a, b in combinations(GATE_IDS, 2):
        pairs.append({
            "a": a, "b": b,
            "comparable": 560, "total_items": 560, "excluded": 0,
            "abstention_rate": 0.0, "difficulty_distribution": None,
            "kappa": 0.0, "ci_lower": -0.06, "ci_upper": 0.06,
            "passed": True, "reason": None,
        })
    report = {
        "pairs": pairs,
        "pair_count": EXPECTED_PAIR_COUNT,
        "all_passed": True,
        "failed_pairs": [],
        "total_items": 560,
        "difficulty_distribution": None,
        "min_comparable": 100,
        "kappa_threshold": 0.2,
        "ci_level": 0.995,
        "n_resamples": 10000,
        "registry": {"lineage_ids": list(GATE_IDS), "unavailable": []},
        "eval_set": {
            "source": "operator", "marker": None,
            "path": "var/operator/eval_set_independence/manifest.json",
            "items": 560,
            "subset_counts": {"general": 210, "l6_enriched": 210,
                              "l4_enriched": 70, "l7_enriched": 70},
        },
    }
    report.update(overrides)
    return report


def _write_independence_report(var_dir, **overrides):
    manifest_path = (
        var_dir / "operator" / "eval_set_independence" / "manifest.json"
    )
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps({
        "eval_set": "independence",
        "provenance": RECORDER_PROVENANCE,
        "items": [{"fixture": True}],
    }, sort_keys=True).encode()
    manifest_path.write_bytes(manifest_bytes)
    report = _independence_report(**overrides)
    report["eval_set"]["validation_set_hash"] = hashlib.sha256(
        manifest_bytes
    ).hexdigest()
    report["eval_set"]["provenance"] = dict(RECORDER_PROVENANCE)
    path = var_dir / INDEPENDENCE_REPORT_NAME
    path.write_text(json.dumps(report))
    return path


def _calibration_record(lineage_id, **overrides):
    record = {
        "lineage_id": lineage_id,
        "calibrated": True,
        "reason": None,
        "method": (
            "platt_temperature" if lineage_id.startswith("l6")
            else "isotonic"
        ),
        "comparable_count": 140,
        "total_items": 140,
        "abstention_rate": 0.0,
        "accuracy": 0.75,
        "precision": 0.7,
        "recall": 0.7,
        "parameters": (
            {"temperature": 1.0} if lineage_id.startswith("l6")
            else {"isotonic": [[0.35, 0.0], [0.85, 1.0]]}
        ),
        "eval_set": {
            "source": "operator", "marker": None,
            "path": "var/operator/eval_set/manifest.json",
            "items": 140,
            "subset_counts": {"general": 70, "l6_enriched": 70},
            "l6_covered_count": 70,
            "validation_set_hash": "0" * 64,
        },
    }
    record.update(overrides)
    return record


def _write_calibration_records(var_dir, mutate=None):
    manifest_path = var_dir / "operator" / "eval_set" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_bytes = json.dumps({
        "eval_set": "calibration",
        "provenance": RECORDER_PROVENANCE,
        "items": [{"fixture": True}],
    }, sort_keys=True).encode()
    manifest_path.write_bytes(manifest_bytes)
    manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()
    cal_dir = var_dir / CALIBRATION_DIR_NAME
    cal_dir.mkdir(parents=True, exist_ok=True)
    for lid in GATE_IDS:
        record = _calibration_record(lid)
        record["eval_set"]["validation_set_hash"] = manifest_hash
        record["eval_set"]["provenance"] = dict(RECORDER_PROVENANCE)
        if mutate is not None:
            mutate(lid, record)
        (cal_dir / f"{lid}.json").write_text(json.dumps(record))
    return cal_dir


class TestCondition4StoredIndependenceReport:
    def test_missing_report_fails_closed(self, tmp_path):
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "independence_report_missing"

    def test_smoke_run_resample_count_rejected(self, tmp_path):
        # Round-3 reviewer note: a sub-protocol certification run (e.g.
        # 50 resamples) stored at the canonical path must not satisfy
        # live rigor — the report's protocol parameters are re-checked
        # against rigor_config.json (10000/0.995/100).
        _write_independence_report(tmp_path, n_resamples=50)
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "protocol_mismatch:n_resamples"

    def test_wrong_ci_level_rejected(self, tmp_path):
        _write_independence_report(tmp_path, ci_level=0.95)
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "protocol_mismatch:ci_level"

    def test_lowered_min_comparable_rejected(self, tmp_path):
        _write_independence_report(tmp_path, min_comparable=50)
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "protocol_mismatch:min_comparable"

    def test_synthetic_marker_rejected(self, tmp_path):
        # §3.2 pt 8: the CI fallback set is not production evidence.
        report = _independence_report()
        report["eval_set"]["marker"] = (
            "synthetic_ci_not_production_independence"
        )
        report["eval_set"]["source"] = "synthetic"
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text(
            json.dumps(report)
        )
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "synthetic_eval_set_not_production"

    def test_missing_eval_set_provenance_rejected(self, tmp_path):
        report = _independence_report()
        del report["eval_set"]
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text(
            json.dumps(report)
        )
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "eval_set_source_not_operator"

    def test_pair_count_mismatch_rejected(self, tmp_path):
        report = _independence_report()
        report["pairs"] = report["pairs"][:20]
        report["pair_count"] = 20
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text(
            json.dumps(report)
        )
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "pair_count_mismatch"

    def test_failing_pair_rejected_even_if_flagged_passed(self, tmp_path):
        # The validator re-derives pair outcomes from the raw CI values:
        # a hand-edited "passed": True with ci_upper >= 0.2 still fails.
        report = _independence_report()
        report["pairs"][0]["ci_upper"] = 0.3
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text(
            json.dumps(report)
        )
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason.startswith("pair_failed:")

    def test_pair_insufficient_comparable_rejected(self, tmp_path):
        report = _independence_report()
        report["pairs"][3]["comparable"] = 99
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text(
            json.dumps(report)
        )
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason.startswith("pair_insufficient_comparable:")

    def test_valid_operator_report_accepted(self, tmp_path):
        _write_independence_report(tmp_path)
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is True
        assert reason is None

    def test_manifest_changed_after_report_is_rejected(self, tmp_path):
        _write_independence_report(tmp_path)
        manifest = (
            tmp_path / "operator" / "eval_set_independence" / "manifest.json"
        )
        manifest.write_text(manifest.read_text() + "\n")
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "independence_manifest_hash_mismatch"

    def test_non_sandbox_recorder_is_rejected(self, tmp_path):
        _write_independence_report(tmp_path)
        report_path = tmp_path / INDEPENDENCE_REPORT_NAME
        report = json.loads(report_path.read_text())
        report["eval_set"]["provenance"]["recorder"] = "hand_edited"
        report_path.write_text(json.dumps(report))
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "independence_recorder_provenance_invalid"

    def test_invalid_json_fails_closed(self, tmp_path):
        (tmp_path / INDEPENDENCE_REPORT_NAME).write_text("{not json")
        valid, reason = validate_stored_independence_report(tmp_path)
        assert valid is False
        assert reason == "independence_report_invalid_json"


class TestCondition5StoredCalibration:
    def test_missing_records_fail_closed(self, tmp_path):
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == f"calibration_missing:{GATE_IDS[0]}"

    def test_valid_records_accepted(self, tmp_path):
        _write_calibration_records(tmp_path)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is True
        assert reason is None

    def test_low_accuracy_rejected(self, tmp_path):
        def mutate(lid, record):
            if lid == "l3-geometric":
                record["accuracy"] = 0.59
        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == "calibration_accuracy_below_threshold:l3-geometric"

    def test_insufficient_comparable_rejected(self, tmp_path):
        def mutate(lid, record):
            if lid == "l7-audio":
                record["comparable_count"] = 79
        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == "calibration_insufficient_comparable:l7-audio"

    def test_uncalibrated_flag_rejected(self, tmp_path):
        def mutate(lid, record):
            if lid == "l2-spectral":
                record["calibrated"] = False
                record["reason"] = "accuracy_below_threshold"
        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == "calibration_not_calibrated:l2-spectral"

    def test_synthetic_marker_rejected(self, tmp_path):
        def mutate(lid, record):
            record["eval_set"]["marker"] = (
                "synthetic_ci_not_production_calibration"
            )
            record["eval_set"]["source"] = "synthetic"
        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == f"calibration_synthetic:{GATE_IDS[0]}"

    def test_lineage_id_mismatch_rejected(self, tmp_path):
        def mutate(lid, record):
            if lid == "l4-metadata":
                record["lineage_id"] = "l5-trajectory"
        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == "calibration_lineage_mismatch:l4-metadata"

    def test_missing_single_record_fails_closed(self, tmp_path):
        _write_calibration_records(tmp_path)
        (tmp_path / CALIBRATION_DIR_NAME / "l6-resnet50.json").unlink()
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == "calibration_missing:l6-resnet50"

    def test_manifest_changed_after_calibration_is_rejected(self, tmp_path):
        _write_calibration_records(tmp_path)
        manifest = tmp_path / "operator" / "eval_set" / "manifest.json"
        manifest.write_text(manifest.read_text() + "\n")
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == f"calibration_manifest_hash_mismatch:{GATE_IDS[0]}"

    def test_non_sandbox_calibration_recorder_is_rejected(self, tmp_path):
        def mutate(_lid, record):
            record["eval_set"]["provenance"]["recorder"] = "hand_edited"

        _write_calibration_records(tmp_path, mutate=mutate)
        valid, reason = validate_stored_calibration(tmp_path)
        assert valid is False
        assert reason == (
            f"calibration_recorder_provenance_invalid:{GATE_IDS[0]}"
        )


class TestEvaluateOperatorGates:
    def test_empty_var_dir_fails_closed(self, tmp_path):
        # §3.2 pt 8: absent operator eval sets -> conditions 4/5 False
        # (not a lesser "operator_gated" state) -> live rigor blocked.
        gates = evaluate_operator_gates(tmp_path)
        assert gates.independence_validated is False
        assert gates.calibration_valid is False
        assert gates.independence_reason == "independence_report_missing"
        assert gates.calibration_reason.startswith("calibration_missing:")
        assert gates.to_dict()["independence_validated"] is False

    def test_fully_certified_var_dir_passes_both_gates(self, tmp_path):
        _write_independence_report(tmp_path)
        _write_calibration_records(tmp_path)
        gates = evaluate_operator_gates(tmp_path)
        assert gates.independence_validated is True
        assert gates.calibration_valid is True
        assert gates.independence_reason is None
        assert gates.calibration_reason is None

    def test_live_rigor_passes_with_certified_gates(self, tmp_path):
        # End-to-end wiring: unanimous outputs + gates derived from the
        # stored artifacts -> all six live conditions met (ALRC path).
        _write_independence_report(tmp_path)
        _write_calibration_records(tmp_path)
        gates = evaluate_operator_gates(tmp_path)
        result = compute_concordance(
            unanimous(),
            runtime="live",
            independence_validated=gates.independence_validated,
            calibration_valid=gates.calibration_valid,
        )
        assert result.passed is True
        assert result.conditions[4] is True
        assert result.conditions[5] is True

    def test_live_rigor_blocked_by_smoke_run_report(self, tmp_path):
        # A 50-resample smoke run stored at the canonical path plus valid
        # calibration: condition 4 stays False, live rigor blocked.
        _write_independence_report(tmp_path, n_resamples=50)
        _write_calibration_records(tmp_path)
        gates = evaluate_operator_gates(tmp_path)
        assert gates.independence_validated is False
        assert gates.calibration_valid is True
        result = compute_concordance(
            unanimous(),
            runtime="live",
            independence_validated=gates.independence_validated,
            calibration_valid=gates.calibration_valid,
        )
        assert result.passed is False
        assert "4" in result.reason
