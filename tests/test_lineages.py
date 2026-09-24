"""Lineage package foundations (spec §3.2): taxonomy, evidence vocabulary,
claim contract, frame sampling, registry declarations, independence
statistics. Lineage *implementations* (l1..l7) land in a later round; the
registry tests here assert honest unavailability until they exist.
"""
from __future__ import annotations

import json
import statistics

import pytest

from uapvf.lineages import (
    EvidenceClaims,
    LineageOutput,
    ProtocolError,
    TAXONOMY,
    is_valid_taxonomy,
    make_non_comparable,
)
from uapvf.lineages.evidence_vocabulary import (
    VocabularyError,
    available_shared_claims,
    available_specific_claims,
    load_vocabulary,
    select_claims,
    shared_claim_emitters,
    validate_claim_contract,
)
from uapvf.lineages.frame_sampling import (
    LINEAGE_FRAME_COUNTS,
    sample_frames,
    seed_from_case_id,
    uniform_indices,
)
from uapvf.lineages.eval_set import (
    SYNTHETIC_MARKER,
    EvalSetError,
    load_independence_eval_set,
    synthetic_independence_eval_set,
)
from uapvf.lineages.independence import (
    bootstrap_kappa_ci,
    cohens_kappa,
    validate_independence,
)
from uapvf.lineages.registry import (
    LINEAGE_DECLARATIONS,
    declared_lineage_ids,
    load_registry,
    pairs_with_zero_shared_input,
)
from uapvf.rigor import load_refutation_rules


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------


def test_taxonomy_exactly_10_categories():
    assert len(TAXONOMY) == 10
    assert TAXONOMY == frozenset(
        {
            "aircraft",
            "satellite",
            "balloon",
            "drone",
            "bird",
            "insect",
            "meteor",
            "lens_artifact",
            "sensor_artifact",
            "astronomical",
        }
    )
    # No unknown, no weather (weather is a battery condition, not a lineage
    # classification; spec §3.2).
    assert not is_valid_taxonomy("unknown")
    assert not is_valid_taxonomy("weather")
    assert not is_valid_taxonomy("")
    assert not is_valid_taxonomy(None)


def test_lineage_output_rejects_out_of_taxonomy():
    with pytest.raises(ProtocolError):
        LineageOutput(lineage_id="l1-photometric", status="ok",
                      classification="flying_saucer")
    with pytest.raises(ProtocolError):
        LineageOutput(lineage_id="l1-photometric", status="sometimes")


def test_lineage_output_dict_roundtrip():
    out = LineageOutput(
        lineage_id="l1-photometric",
        status="ok",
        classification="aircraft",
        artifact_detected=False,
        evidence_claims=EvidenceClaims(
            shared=["point_source", "luminous", "anomalous_signature"],
            specific=[
                "luminance_profile_consistent_with_known_object",
                "high_luminance_ratio",
            ],
        ),
        confidence=0.72,
        defense_claims=["high_luminance_ratio"],
        rationale="periodic bright spots",
        frame_indices_used=[0, 33, 66],
    )
    data = out.to_dict()
    back = LineageOutput.from_dict(json.loads(json.dumps(data)))
    assert back == out
    assert data["evidence_claims"]["shared"] == [
        "point_source", "luminous", "anomalous_signature"
    ]


def test_video_frame_provenance_has_timestamps():
    from uapvf.lineages.subprocess_worker import _attach_frame_provenance

    payload = _attach_frame_provenance(
        {"frame_indices_used": [0, 15, 30], "provenance": {}}, 30.0
    )
    assert payload["provenance"]["frame_indices_used"] == [0, 15, 30]
    assert payload["provenance"]["frame_timestamps_seconds"] == [0.0, 0.5, 1.0]
    assert payload["provenance"]["timestamp_basis"] == "frame_index/source_fps"


def test_null_classification_with_ok_status_is_valid():
    # OBL-13: lineage ran, contributed evidence, asserts no category.
    out = LineageOutput(
        lineage_id="l7-audio", status="ok", classification=None
    )
    assert out.classification is None
    assert LineageOutput.from_dict(out.to_dict()).classification is None


def test_make_non_comparable():
    out = make_non_comparable("l4-metadata")
    assert out.status == "non_comparable"
    assert out.classification is None
    assert out.abstention_reason == "insufficient_evidence_for_claim_emission"


# ---------------------------------------------------------------------------
# Evidence vocabulary
# ---------------------------------------------------------------------------


def test_vocabulary_has_34_claims():
    vocab = load_vocabulary()
    assert len(vocab["shared_claims"]) == 14
    assert len(vocab["specific_claims"]) == 20
    emitters = shared_claim_emitters(vocab)
    assert len(emitters) == 14
    assert all(emitters[c] for c in emitters)


def test_vocabulary_structural_validation(tmp_path):
    bad = tmp_path / "vocab.json"
    payload = {"shared_claims": [], "specific_claims": []}
    bad.write_text(json.dumps(payload))
    with pytest.raises(VocabularyError):
        load_vocabulary(bad)
    with pytest.raises(VocabularyError):
        load_vocabulary(tmp_path / "missing.json")


def test_every_lineage_can_satisfy_claim_contract():
    """§3.2: the 3 shared + 2 specific contract must be satisfiable for all
    7 lineages from their available pools."""
    vocab = load_vocabulary()
    expected_available_shared = {
        # l1 per the §3.2 allowed-emitters table; l4 has exactly 3.
        "l1-photometric": 4,
        "l2-spectral": 4,
        "l3-geometric": 8,
        "l4-metadata": 3,
        "l5-trajectory": 4,
        "l6-resnet50": 8,
        "l7-audio": 5,
    }
    for lineage_id in declared_lineage_ids():
        shared = available_shared_claims(vocab, lineage_id)
        specific = available_specific_claims(vocab, lineage_id)
        assert len(shared) == expected_available_shared[lineage_id], lineage_id
        assert len(shared) >= 3, f"{lineage_id} cannot emit 3 shared claims"
        assert len(specific) >= 2, f"{lineage_id} cannot emit 2 specific claims"


def test_l4_available_shared_is_exactly_spec_set():
    vocab = load_vocabulary()
    assert available_shared_claims(vocab, "l4-metadata") == [
        "anomalous_signature",
        "metadata_anomaly",
        "temporal_consistency",
    ]


def test_claim_selection_is_deterministic_lowest_number_first():
    vocab = load_vocabulary()
    supported_shared = available_shared_claims(vocab, "l3-geometric")
    supported_specific = available_specific_claims(vocab, "l3-geometric")
    first = select_claims(vocab, "l3-geometric", supported_shared,
                          supported_specific)
    second = select_claims(vocab, "l3-geometric", supported_shared,
                           supported_specific)
    assert first == second
    shared, specific = first
    assert shared == ["point_source", "extended_source", "motion_detected"]
    assert specific == ["geometric_contour_matched",
                        "perspective_consistent_with_range"]


def test_claim_selection_insufficient_returns_none():
    vocab = load_vocabulary()
    # Only 2 supported shared claims -> cannot satisfy the 3-shared contract.
    result = select_claims(
        vocab, "l1-photometric", ["point_source", "luminous"],
        available_specific_claims(vocab, "l1-photometric"),
    )
    assert result is None
    assert select_claims(vocab, "l1-photometric", [], []) is None


def test_validate_claim_contract_pass_and_violations():
    vocab = load_vocabulary()
    good = LineageOutput(
        lineage_id="l1-photometric",
        status="ok",
        classification="aircraft",
        evidence_claims=EvidenceClaims(
            shared=["point_source", "extended_source", "luminous"],
            specific=["luminance_profile_consistent_with_known_object",
                      "high_luminance_ratio"],
        ),
        defense_claims=["high_luminance_ratio"],
    )
    assert validate_claim_contract(good, vocab) == []

    wrong_count = LineageOutput(
        lineage_id="l1-photometric",
        status="ok",
        classification="aircraft",
        evidence_claims=EvidenceClaims(
            shared=["point_source", "extended_source", "luminous",
                    "anomalous_signature"],
            specific=["high_luminance_ratio"],
        ),
    )
    errors = validate_claim_contract(wrong_count, vocab)
    assert any("shared" in e for e in errors)
    assert any("specific" in e for e in errors)

    foreign = LineageOutput(
        lineage_id="l1-photometric",
        status="ok",
        classification="aircraft",
        evidence_claims=EvidenceClaims(
            shared=["point_source", "extended_source", "motion_detected"],
            specific=["luminance_profile_consistent_with_known_object",
                      "high_luminance_ratio"],
        ),
    )
    assert any("not in its allowed emitter pool" in e
               for e in validate_claim_contract(foreign, vocab))

    defense_outside = LineageOutput(
        lineage_id="l1-photometric",
        status="ok",
        classification="aircraft",
        evidence_claims=EvidenceClaims(
            shared=["point_source", "extended_source", "luminous"],
            specific=["luminance_profile_consistent_with_known_object",
                      "high_luminance_ratio"],
        ),
        defense_claims=["temporal_consistency"],
    )
    assert any("defense claim" in e
               for e in validate_claim_contract(defense_outside, vocab))

    abstaining_with_claims = LineageOutput(
        lineage_id="l1-photometric",
        status="non_comparable",
        evidence_claims=EvidenceClaims(shared=["point_source"]),
    )
    assert validate_claim_contract(abstaining_with_claims, vocab)


def test_defense_resolutions_reference_emittable_claims():
    """OBL-90: every defense_resolution must name a defense claim the
    challenged lineage (owner of the challenged specific claim) can emit."""
    vocab = load_vocabulary()
    rules = load_refutation_rules()
    owner = {c["claim_id"]: c["lineage"] for c in vocab["specific_claims"]}
    for resolution in rules["defense_resolutions"]:
        challenged_lineage = owner[resolution["challenged_claim"]]
        pool = set(available_shared_claims(vocab, challenged_lineage)) | set(
            available_specific_claims(vocab, challenged_lineage)
        )
        assert resolution["defense_claim"] in pool, resolution


# ---------------------------------------------------------------------------
# Frame sampling
# ---------------------------------------------------------------------------


def test_uniform_indices_spec_formula():
    assert uniform_indices(300, 10) == [i * 30 for i in range(10)]
    assert uniform_indices(297, 10) == [int(i * 297 / 10) for i in range(10)]
    # Fewer frames than the cap: use all frames.
    assert uniform_indices(5, 10) == [0, 1, 2, 3, 4]
    assert uniform_indices(10, 10) == list(range(10))
    assert uniform_indices(0, 10) == []
    assert uniform_indices(300, 0) == []


def test_lineage_frame_counts_match_spec():
    assert LINEAGE_FRAME_COUNTS == {
        "l1-photometric": 10,
        "l2-spectral": 10,
        "l3-geometric": 10,
        "l4-metadata": 0,
        "l5-trajectory": 60,
        "l6-resnet50": 5,
        "l7-audio": 0,
    }


def test_sample_frames_deterministic_and_probe_required():
    seed = seed_from_case_id("case-123")
    a = sample_frames("/media/x.mp4", 10, seed, frame_count=300)
    b = sample_frames("/media/x.mp4", 10, seed, frame_count=300)
    assert a == b == [i * 30 for i in range(10)]
    with pytest.raises(ValueError):
        sample_frames("/media/x.mp4", 10, seed)  # no in-process media parsing
    with pytest.raises(ValueError):
        sample_frames("", 10, seed, frame_count=300)
    with pytest.raises(ValueError):
        sample_frames("/media/x.mp4", 10, "", frame_count=300)


def test_seed_from_case_id_is_sha256():
    import hashlib

    assert seed_from_case_id("abc") == hashlib.sha256(b"abc").hexdigest()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_declares_seven_lineages():
    assert len(LINEAGE_DECLARATIONS) == 7
    assert declared_lineage_ids() == [
        "l1-photometric",
        "l2-spectral",
        "l3-geometric",
        "l4-metadata",
        "l5-trajectory",
        "l6-resnet50",
        "l7-audio",
    ]
    frames = {d["lineage_id"]: d["frames"] for d in LINEAGE_DECLARATIONS}
    assert frames == LINEAGE_FRAME_COUNTS
    modalities = {d["lineage_id"]: d["modality"] for d in LINEAGE_DECLARATIONS}
    assert modalities["l4-metadata"] == "non_vision"
    assert modalities["l7-audio"] == "non_vision"
    assert modalities["l5-trajectory"] == "physics"
    assert modalities["l6-resnet50"] == "ml_vision"


def test_registry_structural_separation_11_of_21_pairs():
    pairs = pairs_with_zero_shared_input()
    assert len(pairs) == 11
    total_pairs = 7 * 6 // 2
    assert total_pairs == 21
    for a, b in pairs:
        inputs = {
            d["lineage_id"]: d["input_modality"] for d in LINEAGE_DECLARATIONS
        }
        assert inputs[a] != inputs[b]


def test_load_registry_honest_unavailability():
    # Implementations land in a later round; until then the registry must
    # report unavailability honestly, never substitute silently.
    entries = load_registry()
    assert len(entries) == 7
    for entry in entries:
        try:
            __import__(entry.declaration["module"])
            implemented = True
        except ImportError:
            implemented = False
        assert entry.available == implemented
        if not implemented:
            assert entry.unavailable_reason
    missing = [e.lineage_id for e in entries if not e.available]
    if missing:
        with pytest.raises(LookupError):
            load_registry(strict=True)


# ---------------------------------------------------------------------------
# Independence statistics
# ---------------------------------------------------------------------------


def test_cohens_kappa_hand_computed():
    a = [0, 1, 0, 1, 0]
    b = [0, 1, 1, 1, 0]
    # po = 0.8, pe = 0.48 -> kappa = 0.32 / 0.52
    assert cohens_kappa(a, b) == pytest.approx(0.32 / 0.52)
    assert cohens_kappa([1, 2, 3], [1, 2, 3]) == pytest.approx(1.0)
    # Degenerate: both constant, different labels -> no agreement beyond
    # chance; treated as 0.0 (fail closed), not a division by zero.
    assert cohens_kappa([1, 1, 1], [2, 2, 2]) == 0.0
    with pytest.raises(ValueError):
        cohens_kappa([1, 2], [1])
    with pytest.raises(ValueError):
        cohens_kappa([], [])


def test_bootstrap_ci_deterministic():
    a = [str(i % 4) for i in range(120)]
    b = [str((i * 3 + 1) % 4) for i in range(120)]
    r1 = bootstrap_kappa_ci(a, b, n_resamples=500, seed=7)
    r2 = bootstrap_kappa_ci(a, b, n_resamples=500, seed=7)
    assert r1 == r2
    assert r1["ci_lower"] <= r1["ci_upper"]
    assert -1.0 <= r1["ci_lower"] and r1["ci_upper"] <= 1.0
    r3 = bootstrap_kappa_ci(a, b, n_resamples=500, seed=8)
    assert r3["ci_upper"] != r1["ci_upper"] or r3["ci_lower"] != r1["ci_lower"]


def _orthogonal_eval_set(n_items=560):
    """Seven deterministic classifiers whose labels are mutually
    uncorrelated: lineage k emits ``(i*(k+1) + k) % 7``. Distinct multipliers
    mod 7 give po == pe == 1/7 for every pair, so kappa == 0 exactly (n is a
    multiple of 7)."""
    items = []
    for i in range(n_items):
        item = {}
        for k, lineage_id in enumerate(declared_lineage_ids()):
            item[lineage_id] = f"cat{(i * (k + 1) + k) % 7}"
        items.append(item)
    return items


def test_validate_independence_passes_on_low_agreement():
    report = validate_independence(
        _orthogonal_eval_set(),
        min_comparable=100,
        n_resamples=400,
        seed=11,
    )
    assert report["pair_count"] == 21
    assert report["all_passed"] is True
    assert report["failed_pairs"] == []
    for pair in report["pairs"]:
        assert pair["comparable"] >= 100
        assert pair["ci_upper"] < 0.2


def test_validate_independence_rejects_identical_lineages():
    """AC-2 negative test: identical dummy lineages must be rejected."""
    items = []
    for i in range(150):
        label = f"cat{i % 3}"
        items.append({lid: label for lid in declared_lineage_ids()})
    report = validate_independence(items, min_comparable=100,
                                   n_resamples=200, seed=11)
    assert report["all_passed"] is False
    assert len(report["failed_pairs"]) == 21
    for pair in report["pairs"]:
        assert pair["kappa"] == pytest.approx(1.0)


def test_validate_independence_insufficient_comparable_fails_closed():
    items = [
        {lid: f"cat{i % 3}" for lid in declared_lineage_ids()}
        for i in range(20)
    ]
    report = validate_independence(items, min_comparable=100,
                                   n_resamples=100, seed=11)
    assert report["all_passed"] is False
    for pair in report["pairs"]:
        assert pair["passed"] is False
        assert "insufficient_comparable" in pair["reason"]


def test_validate_independence_excludes_abstentions():
    items = []
    for i in range(300):
        item = {lid: f"cat{i % 4}" for lid in declared_lineage_ids()}
        if i % 2 == 0:
            item["l7-audio"] = None  # abstains on half the items
        items.append(item)
    report = validate_independence(items, min_comparable=100,
                                   n_resamples=200, seed=11)
    audio_pairs = [p for p in report["pairs"] if p["b"] == "l7-audio"]
    assert audio_pairs
    for pair in audio_pairs:
        assert pair["comparable"] == 150
        # AC-2: per-pair abstention rate reported.
        assert pair["total_items"] == 300
        assert pair["excluded"] == 150
        assert pair["abstention_rate"] == pytest.approx(0.5)
    others = [p for p in report["pairs"] if p["b"] != "l7-audio"]
    for pair in others:
        assert pair["comparable"] == 300
        assert pair["excluded"] == 0
        assert pair["abstention_rate"] == 0.0
    # No difficulty channel supplied -> distribution fields are None.
    assert report["difficulty_distribution"] is None
    for pair in report["pairs"]:
        assert pair["difficulty_distribution"] is None


def test_validate_independence_reports_difficulty_distribution():
    """Spec §3.2 pt 6/7 + AC-2: the report carries the case difficulty
    distribution supplied through the item_difficulties channel — at report
    level (over all items, so the ≥30% hard general-item composition is
    auditable) and per pair (over that pair's comparable items)."""
    items = _orthogonal_eval_set(300)
    difficulties = []
    for i in range(300):
        if i < 100:  # general items, 34% hard
            difficulties.append("hard" if i % 3 == 0 else "easy")
        elif i < 200:
            difficulties.append("l6_enriched")
        elif i < 250:
            difficulties.append("l4_enriched")
        else:
            difficulties.append("l7_enriched")
    report = validate_independence(
        items,
        item_difficulties=difficulties,
        min_comparable=100,
        n_resamples=200,
        seed=11,
    )
    expected = {
        "hard": 34,
        "easy": 66,
        "l6_enriched": 100,
        "l4_enriched": 50,
        "l7_enriched": 50,
    }
    assert report["difficulty_distribution"] == expected
    # No abstentions in the orthogonal set: per-pair equals report-level.
    for pair in report["pairs"]:
        assert pair["difficulty_distribution"] == expected
        assert pair["abstention_rate"] == 0.0


def test_validate_independence_pair_difficulty_excludes_abstained_items():
    items = _orthogonal_eval_set(200)
    difficulties = ["hard" if i < 80 else "easy" for i in range(200)]
    for i, item in enumerate(items):
        if i < 40:
            item["l7-audio"] = None  # abstains on half the hard items
    report = validate_independence(
        items,
        item_difficulties=difficulties,
        min_comparable=100,
        n_resamples=200,
        seed=11,
    )
    assert report["difficulty_distribution"] == {"hard": 80, "easy": 120}
    audio_pairs = [p for p in report["pairs"] if p["b"] == "l7-audio"]
    assert audio_pairs
    for pair in audio_pairs:
        assert pair["comparable"] == 160
        assert pair["difficulty_distribution"] == {"hard": 40, "easy": 120}
        assert pair["abstention_rate"] == pytest.approx(0.2)


def test_validate_independence_difficulty_length_mismatch_rejected():
    with pytest.raises(ValueError, match="one label per eval item"):
        validate_independence(
            _orthogonal_eval_set(120),
            item_difficulties=["hard"] * 5,
            n_resamples=50,
            seed=11,
        )


def test_bootstrap_default_config_values():
    from uapvf.rigor import load_rigor_config

    cfg = load_rigor_config()
    assert cfg["independence_statistic"] == "cohens_kappa"
    assert cfg["independence_threshold"] == 0.2
    assert cfg["independence_bootstrap_samples"] == 10000
    assert cfg["independence_ci_level"] == 0.995
    assert cfg["independence_min_comparable_items"] == 100
    # Sanity: the defaults are fast enough for CI when overridden.
    assert statistics.pvariance([0.5, 0.5]) == 0.0


# ---------------------------------------------------------------------------
# Independence eval-set management (spec §3.2 pt 1/7/8)
# ---------------------------------------------------------------------------

TAX7 = sorted(TAXONOMY)[:7]


def _operator_eval_items(n_general=100, n_l6=100, n_l4=50, n_l7=50):
    """Operator-manifest items with mutually uncorrelated classifications
    over seven real taxonomy values (same construction as
    ``_orthogonal_eval_set``; item totals that are multiples of 7 give
    kappa == 0 exactly for every pair)."""
    ids = declared_lineage_ids()
    plan = [("general", n_general), ("l6_enriched", n_l6),
            ("l4_enriched", n_l4), ("l7_enriched", n_l7)]
    items = []
    idx = 0
    for subset, count in plan:
        for _ in range(count):
            difficulty = None
            if subset == "general":
                difficulty = "hard" if idx % 3 == 0 else "easy"
            items.append({
                "item_id": f"{subset}-{idx:04d}",
                "subset": subset,
                "difficulty": difficulty,
                "classifications": {
                    lid: TAX7[(idx * (k + 1) + k) % 7]
                    for k, lid in enumerate(ids)
                },
            })
            idx += 1
    return items


def _write_manifest(root, items):
    var_dir = root / "var"
    eval_dir = var_dir / "operator" / "eval_set_independence"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "manifest.json").write_text(
        json.dumps({"eval_set": "independence", "items": items})
    )
    return var_dir


def test_load_independence_eval_set_operator_manifest(tmp_path):
    var_dir = _write_manifest(tmp_path, _operator_eval_items())
    eval_set = load_independence_eval_set(var_dir)
    assert eval_set.source == "operator"
    assert eval_set.marker is None
    assert eval_set.total_items == 300
    assert eval_set.subset_counts == {
        "general": 100, "l6_enriched": 100, "l4_enriched": 50,
        "l7_enriched": 50,
    }
    assert eval_set.path == (
        var_dir / "operator" / "eval_set_independence" / "manifest.json"
    )
    # Difficulty channel: general labels, then enriched subset tags.
    assert eval_set.item_difficulties[0] == "hard"
    assert eval_set.item_difficulties[1] == "easy"
    assert eval_set.item_difficulties[100] == "l6_enriched"
    assert eval_set.item_difficulties[200] == "l4_enriched"
    assert eval_set.item_difficulties[250] == "l7_enriched"


def test_load_independence_eval_set_missing_falls_back_then_fails_closed(
    tmp_path,
):
    # Spec §3.2 pt 8: absent operator set -> marked synthetic CI fallback.
    eval_set = load_independence_eval_set(tmp_path)
    assert eval_set.source == "synthetic"
    assert eval_set.marker == SYNTHETIC_MARKER
    # ...unless the caller opts out (live-mode gating must fail closed).
    with pytest.raises(EvalSetError, match="not found"):
        load_independence_eval_set(tmp_path, fallback_to_synthetic=False)


def test_load_independence_eval_set_rejects_bad_composition(tmp_path):
    # Too few items overall.
    var_dir = _write_manifest(tmp_path, _operator_eval_items()[:50])
    with pytest.raises(EvalSetError, match="requires >= 300"):
        load_independence_eval_set(var_dir)
    # Subset minimum violated (l7_enriched 49 < 50) while the 300-item
    # total is still met, so the subset check is the one that fires.
    var_dir = _write_manifest(
        tmp_path, _operator_eval_items(n_general=101, n_l7=49)
    )
    with pytest.raises(EvalSetError, match="l7_enriched"):
        load_independence_eval_set(var_dir)
    # §3.2 pt 7: general items with no hard/ambiguous cases.
    items = _operator_eval_items()
    for item in items[:100]:
        item["difficulty"] = "easy"
    var_dir = _write_manifest(tmp_path, items)
    with pytest.raises(EvalSetError, match="hard/ambiguous"):
        load_independence_eval_set(var_dir)


def test_load_independence_eval_set_rejects_malformed_items(tmp_path):
    def load(payload):
        var_dir = tmp_path / "var"
        d = var_dir / "operator" / "eval_set_independence"
        d.mkdir(parents=True, exist_ok=True)
        (d / "manifest.json").write_text(json.dumps(payload))
        return load_independence_eval_set(var_dir)

    items = _operator_eval_items()
    bad = _operator_eval_items()
    del bad[0]["classifications"]["l7-audio"]
    with pytest.raises(EvalSetError, match="declared lineage ids"):
        load({"eval_set": "independence", "items": bad})

    bad = _operator_eval_items()
    bad[0]["classifications"]["l1-photometric"] = "flying_saucer"
    with pytest.raises(EvalSetError, match="not in the taxonomy"):
        load({"eval_set": "independence", "items": bad})

    bad = _operator_eval_items()
    bad[1]["item_id"] = bad[0]["item_id"]
    with pytest.raises(EvalSetError, match="duplicate item_id"):
        load({"eval_set": "independence", "items": bad})

    bad = _operator_eval_items()
    bad[0]["subset"] = "mystery"
    with pytest.raises(EvalSetError, match="subset must be one of"):
        load({"eval_set": "independence", "items": bad})

    with pytest.raises(EvalSetError, match='must be "independence"'):
        load({"eval_set": "calibration", "items": items})
    with pytest.raises(EvalSetError, match="non-empty"):
        load({"eval_set": "independence", "items": []})


def test_synthetic_eval_set_is_marked_and_fails_closed():
    eval_set = synthetic_independence_eval_set()
    assert eval_set.source == "synthetic"
    assert eval_set.marker == SYNTHETIC_MARKER
    assert eval_set.total_items == 20
    ids = set(declared_lineage_ids())
    assert all(set(c) == ids for c in eval_set.classifications_by_item)
    report = validate_independence(
        load_registry(), eval_set, n_resamples=50, seed=11
    )
    # < 100 items -> every pair fails the comparable minimum (fail closed).
    assert report["all_passed"] is False
    assert report["pair_count"] == 21
    for pair in report["pairs"]:
        assert "insufficient_comparable" in pair["reason"]
    assert report["eval_set"]["marker"] == SYNTHETIC_MARKER
    assert report["eval_set"]["source"] == "synthetic"


# ---------------------------------------------------------------------------
# Spec-shape validate_independence(registry, independence_eval_set)
# ---------------------------------------------------------------------------


def test_validate_independence_spec_shape_registry_plus_eval_set(tmp_path):
    var_dir = _write_manifest(
        tmp_path, _operator_eval_items(210, 210, 70, 70)
    )
    eval_set = load_independence_eval_set(var_dir)
    registry = load_registry()
    report = validate_independence(
        registry, eval_set, min_comparable=100, n_resamples=400, seed=11
    )
    assert report["pair_count"] == 21
    assert report["all_passed"] is True
    assert report["registry"]["lineage_ids"] == declared_lineage_ids()
    # All seven implementations are now runnable/loadable. Individual runs
    # still report honest non-comparability (for example image-only input to
    # trajectory/audio) or unavailability (uncertified ResNet weights).
    assert report["registry"]["unavailable"] == []
    assert report["eval_set"]["source"] == "operator"
    assert report["eval_set"]["marker"] is None
    assert report["eval_set"]["items"] == 560
    assert report["eval_set"]["subset_counts"] == {
        "general": 210, "l6_enriched": 210, "l4_enriched": 70,
        "l7_enriched": 70,
    }
    # §3.2 pt 6/7 auditable from the report: difficulty distribution with
    # the >=30% hard general composition and enriched-subset sizes.
    assert report["difficulty_distribution"] == {
        "hard": 70, "easy": 140, "l6_enriched": 210, "l4_enriched": 70,
        "l7_enriched": 70,
    }
    for pair in report["pairs"]:
        assert pair["abstention_rate"] == 0.0
        assert pair["passed"] is True


def test_validate_independence_registry_form_rejects_conflicting_channels():
    eval_set = synthetic_independence_eval_set()
    with pytest.raises(ValueError, match="do not pass them separately"):
        validate_independence(
            load_registry(), eval_set, lineage_ids=["l1-photometric"]
        )
    with pytest.raises(ValueError, match="do not pass them separately"):
        validate_independence(
            load_registry(), eval_set, item_difficulties=["hard"] * 20
        )


def test_validate_independence_registry_form_rejects_bad_registry():
    eval_set = synthetic_independence_eval_set()
    with pytest.raises(ValueError, match="registry is empty"):
        validate_independence([], eval_set)
    with pytest.raises(ValueError, match="lineage_id"):
        validate_independence([object()], eval_set)


# ---------------------------------------------------------------------------
# Calibration eval-set management (spec §3.2: separate eval sets)
# ---------------------------------------------------------------------------

from uapvf.lineages.calibration import (  # noqa: E402
    METHOD_ISOTONIC,
    METHOD_PLATT,
    REASON_ACCURACY_BELOW_THRESHOLD,
    REASON_INSUFFICIENT_COMPARABLE,
    apply_calibration,
    calibrate_lineage,
    calibrate_lineages,
    isotonic_fit,
    macro_precision_recall,
    platt_temperature_fit,
)
from uapvf.lineages.eval_set import (  # noqa: E402
    L6_COVERED_CATEGORIES,
    SYNTHETIC_CALIBRATION_MARKER,
    CalibrationEvalSet,
    load_calibration_eval_set,
    synthetic_calibration_eval_set,
)

CAL_IDS = declared_lineage_ids()
CAL_CATS = sorted(TAXONOMY)
CAL_COVERED = sorted(L6_COVERED_CATEGORIES)


def _calibration_items(n_general=70, n_l6=70, null_lineage=None,
                       null_every=None, correct_every=4):
    """Operator calibration manifest items. Every lineage records a
    classification with raw confidence; accuracy is 1 - 1/correct_every
    (deterministic), optionally degraded for one lineage via nulling."""
    items = []
    idx = 0
    for subset, count in (("general", n_general), ("l6_enriched", n_l6)):
        for _ in range(count):
            if subset == "l6_enriched":
                label = CAL_COVERED[idx % 3]
            else:
                label = CAL_CATS[idx % 10]
            cls, confs = {}, {}
            for k, lid in enumerate(CAL_IDS):
                if (
                    lid == null_lineage
                    or (null_every and idx % null_every == 0)
                ):
                    cls[lid] = None
                    continue
                correct = (idx % correct_every) != 0
                pred = label if correct else CAL_CATS[
                    (CAL_CATS.index(label) + 1 + k) % 10
                ]
                cls[lid] = pred
                confs[lid] = 0.85 if correct else 0.35
            items.append({
                "item_id": f"cal-{idx:04d}",
                "subset": subset,
                "label": label,
                "classifications": cls,
                "confidences": confs,
            })
            idx += 1
    return items


def _write_calibration_manifest(root, items):
    var_dir = root / "var"
    eval_dir = var_dir / "operator" / "eval_set"
    eval_dir.mkdir(parents=True, exist_ok=True)
    (eval_dir / "manifest.json").write_text(
        json.dumps({"eval_set": "calibration", "items": items})
    )
    return var_dir


def test_load_calibration_eval_set_operator_manifest(tmp_path):
    var_dir = _write_calibration_manifest(tmp_path, _calibration_items())
    eval_set = load_calibration_eval_set(var_dir)
    assert eval_set.source == "operator"
    assert eval_set.marker is None
    assert eval_set.total_items == 140
    assert eval_set.subset_counts == {"general": 70, "l6_enriched": 70}
    assert eval_set.l6_covered_count >= 60
    assert eval_set.path == (
        var_dir / "operator" / "eval_set" / "manifest.json"
    )
    # Validation-set hash: 64 hex chars of the manifest bytes.
    assert len(eval_set.validation_set_hash) == 64
    int(eval_set.validation_set_hash, 16)
    # Confidence channel mirrors classifications (null <-> null).
    first = eval_set.classifications_by_item[0]
    assert all(lid in first for lid in CAL_IDS)
    assert set(eval_set.confidences_by_item[0]) == set(CAL_IDS)


def test_load_calibration_eval_set_missing_falls_back_then_fails_closed(
    tmp_path,
):
    # Same fail-closed pattern as the independence set: absent operator
    # set -> marked synthetic CI fallback; opt-out raises.
    eval_set = load_calibration_eval_set(tmp_path)
    assert eval_set.source == "synthetic"
    assert eval_set.marker == SYNTHETIC_CALIBRATION_MARKER
    assert eval_set.total_items == 20
    assert len(eval_set.validation_set_hash) == 64
    with pytest.raises(EvalSetError, match="not found"):
        load_calibration_eval_set(tmp_path, fallback_to_synthetic=False)


def test_load_calibration_eval_set_rejects_bad_composition(tmp_path):
    # Too few items overall.
    var_dir = _write_calibration_manifest(
        tmp_path, _calibration_items(n_general=40, n_l6=59)
    )
    with pytest.raises(EvalSetError, match="requires >= 100"):
        load_calibration_eval_set(var_dir)
    # Enough items, too few in l6-covered categories: general labels cycle
    # the full taxonomy, so 100 general + 0 enriched leaves only the
    # covered-category general items (~30) below the 60 minimum.
    items = _calibration_items(n_general=100, n_l6=0)
    var_dir = _write_calibration_manifest(tmp_path, items)
    with pytest.raises(EvalSetError, match="l6-covered"):
        load_calibration_eval_set(var_dir)


def test_load_calibration_eval_set_rejects_malformed_items(tmp_path):
    def load_with(mutate):
        items = _calibration_items()
        mutate(items)
        var_dir = _write_calibration_manifest(tmp_path, items)
        return load_calibration_eval_set(var_dir)

    # l6_enriched item with a non-covered label.
    def bad_l6_label(items):
        items[70]["label"] = "meteor"
    with pytest.raises(EvalSetError, match="l6-covered category"):
        load_with(bad_l6_label)

    # Missing ground-truth label (null label is rejected: calibration
    # needs ground truth).
    def null_label(items):
        items[0]["label"] = None
    with pytest.raises(EvalSetError, match="label must be a taxonomy"):
        load_with(null_label)

    # Out-of-taxonomy label.
    def alien_label(items):
        items[0]["label"] = "weather"
    with pytest.raises(EvalSetError, match="label must be a taxonomy"):
        load_with(alien_label)

    # Classifications keys must be exactly the declared lineage ids.
    def extra_lineage_key(items):
        items[0]["classifications"]["l8-imaginary"] = "bird"
    with pytest.raises(EvalSetError, match="declared lineage ids"):
        load_with(extra_lineage_key)

    # Non-null classification without a confidence.
    def missing_confidence(items):
        del items[0]["confidences"]["l1-photometric"]
    with pytest.raises(EvalSetError, match="requires a numeric confidence"):
        load_with(missing_confidence)

    # Confidence outside [0, 1].
    def confidence_out_of_range(items):
        items[0]["confidences"]["l1-photometric"] = 1.5
    with pytest.raises(EvalSetError, match="outside \\[0, 1\\]"):
        load_with(confidence_out_of_range)

    # Abstention with a confidence is inconsistent recorded data.
    def confidence_without_classification(items):
        items[0]["classifications"]["l1-photometric"] = None
    with pytest.raises(EvalSetError, match="abstained"):
        load_with(confidence_without_classification)

    # Wrong eval_set kind (e.g. pointing the independence manifest here).
    items = _calibration_items()
    var_dir = _write_calibration_manifest(tmp_path, items)
    path = var_dir / "operator" / "eval_set" / "manifest.json"
    payload = json.loads(path.read_text())
    payload["eval_set"] = "independence"
    path.write_text(json.dumps(payload))
    with pytest.raises(EvalSetError, match='"calibration"'):
        load_calibration_eval_set(var_dir)


# ---------------------------------------------------------------------------
# Calibration statistics (spec §3.2: abstention-aware accuracy, fits)
# ---------------------------------------------------------------------------


def _direct_eval_set(items):
    """CalibrationEvalSet straight from manifest items (no filesystem)."""
    labels = [i["label"] for i in items]
    return CalibrationEvalSet(
        labels=labels,
        classifications_by_item=[i["classifications"] for i in items],
        confidences_by_item=[i["confidences"] for i in items],
        item_ids=[i["item_id"] for i in items],
        subset_counts={"general": 0, "l6_enriched": 0},
        l6_covered_count=sum(
            1 for l in labels if l in L6_COVERED_CATEGORIES
        ),
        source="operator",
        marker=None,
        path=None,
        validation_set_hash="0" * 64,
    )


def test_calibrate_lineage_passes_with_accuracy_and_comparables():
    eval_set = _direct_eval_set(_calibration_items())
    result = calibrate_lineage("l1-photometric", eval_set)
    assert result.calibrated is True
    assert result.reason is None
    assert result.method == METHOD_ISOTONIC
    assert result.comparable_count == 140
    assert result.accuracy == pytest.approx(0.75)
    assert result.abstention_rate == 0.0
    assert 0.0 <= result.precision <= 1.0
    assert 0.0 <= result.recall <= 1.0
    # Isotonic parameters: sorted knots, non-decreasing values.
    knots = result.parameters["isotonic"]
    assert knots and all(len(k) == 2 for k in knots)
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    assert xs == sorted(xs)
    assert ys == sorted(ys)


def test_calibrate_lineage_l6_uses_platt_temperature():
    eval_set = _direct_eval_set(_calibration_items())
    result = calibrate_lineage("l6-resnet50", eval_set)
    assert result.calibrated is True
    assert result.method == METHOD_PLATT
    temperature = result.parameters["temperature"]
    assert temperature > 0
    # Calibrated probabilities are monotone in raw confidence.
    lo = apply_calibration(0.3, result.parameters)
    hi = apply_calibration(0.9, result.parameters)
    assert 0.0 <= lo <= hi <= 1.0


def test_calibrate_lineage_insufficient_comparable_fails_closed():
    # Null every classification for l4 -> 0 comparable < 80.
    items = _calibration_items(null_lineage="l4-metadata")
    result = calibrate_lineage("l4-metadata", _direct_eval_set(items))
    assert result.calibrated is False
    assert result.reason == REASON_INSUFFICIENT_COMPARABLE
    assert result.comparable_count == 0
    assert result.accuracy is None
    assert result.parameters == {}
    # 70 of 140 nulled -> 70 comparable, still < 80.
    items = _calibration_items(null_every=2)
    result = calibrate_lineage("l2-spectral", _direct_eval_set(items))
    assert result.calibrated is False
    assert result.reason == REASON_INSUFFICIENT_COMPARABLE
    assert result.comparable_count == 70
    assert result.abstention_rate == pytest.approx(0.5)


def test_calibrate_lineage_accuracy_below_threshold_fails():
    # correct_every=2 -> 50% accuracy < 0.60: fit still produced, but the
    # lineage is not certified.
    items = _calibration_items(correct_every=2)
    result = calibrate_lineage("l3-geometric", _direct_eval_set(items))
    assert result.calibrated is False
    assert result.reason == REASON_ACCURACY_BELOW_THRESHOLD
    assert result.accuracy == pytest.approx(0.5)
    assert result.parameters  # fit ran; certification is what failed


def test_calibrate_lineage_boundary_accuracy_passes():
    # 96 of 160 correct = exactly 0.60 -> calibrated (>= threshold).
    crafted = []
    for idx in range(160):
        label = CAL_CATS[idx % 10]
        cls, confs = {}, {}
        for k, lid in enumerate(CAL_IDS):
            correct = idx % 5 < 3
            pred = label if correct else CAL_CATS[
                (CAL_CATS.index(label) + 1 + k) % 10
            ]
            cls[lid] = pred
            confs[lid] = 0.8 if correct else 0.3
        crafted.append({
            "item_id": f"b-{idx:04d}", "subset": "general",
            "label": label, "classifications": cls, "confidences": confs,
        })
    result = calibrate_lineage("l5-trajectory", _direct_eval_set(crafted))
    assert result.accuracy == pytest.approx(0.6)
    assert result.calibrated is True


def test_calibrate_lineages_runs_all_seven_over_registry():
    eval_set = _direct_eval_set(_calibration_items())
    results = calibrate_lineages(load_registry(), eval_set)
    assert list(results) == CAL_IDS  # declaration order
    assert all(r.calibrated for r in results.values())
    methods = {lid: r.method for lid, r in results.items()}
    assert methods["l6-resnet50"] == METHOD_PLATT
    assert all(
        m == METHOD_ISOTONIC for lid, m in methods.items()
        if lid != "l6-resnet50"
    )


def test_calibrate_lineages_rejects_bad_registry():
    eval_set = _direct_eval_set(_calibration_items())
    with pytest.raises(ValueError, match="registry is empty"):
        calibrate_lineages([], eval_set)
    with pytest.raises(ValueError, match="lineage_id"):
        calibrate_lineages([object()], eval_set)


def test_synthetic_calibration_set_fails_closed_for_all_lineages():
    # Spec: synthetic CI fallback exercises the path but never certifies
    # (20 items < 80 comparable minimum for every lineage).
    eval_set = synthetic_calibration_eval_set()
    results = calibrate_lineages(load_registry(), eval_set)
    for result in results.values():
        assert result.calibrated is False
        assert result.reason == REASON_INSUFFICIENT_COMPARABLE
        assert result.eval_set["marker"] == SYNTHETIC_CALIBRATION_MARKER


def test_platt_temperature_fit_rejects_bad_input():
    with pytest.raises(ValueError, match="equal length"):
        platt_temperature_fit([0.5], [1, 0])
    with pytest.raises(ValueError, match="zero items"):
        platt_temperature_fit([], [])


def test_isotonic_fit_pools_violators_and_is_monotone():
    # Targets 0,1,0 over ascending scores: the last two must pool.
    knots = isotonic_fit([0.1, 0.2, 0.3], [0, 1, 0])
    xs = [k[0] for k in knots]
    ys = [k[1] for k in knots]
    assert xs == sorted(xs)
    assert ys == sorted(ys)
    assert ys[-1] == pytest.approx(0.5)  # pooled mean of 1 and 0
    with pytest.raises(ValueError, match="equal length"):
        isotonic_fit([0.5], [1, 0])


def test_apply_calibration_rejects_unknown_parameters():
    with pytest.raises(ValueError, match="unknown calibration parameters"):
        apply_calibration(0.5, {})
    with pytest.raises(ValueError, match="temperature must be positive"):
        apply_calibration(0.5, {"temperature": 0})


def test_calibration_record_matches_lineage_output_block_shape():
    eval_set = _direct_eval_set(_calibration_items())
    result = calibrate_lineage("l1-photometric", eval_set)
    record = result.calibration_record()
    # Spec §3.2 LineageOutput.calibration block fields.
    assert set(record) == {
        "calibrated", "source", "validation_set_hash", "accuracy",
        "precision", "recall", "abstention_rate", "comparable_count",
    }
    assert record["calibrated"] is True
    assert record["source"] == "operator_eval_set"
    assert record["comparable_count"] == 140


def test_macro_precision_recall_perfect_and_zero_predictions():
    assert macro_precision_recall(["a", "b"], ["a", "b"]) == (1.0, 1.0)
    precision, recall = macro_precision_recall(["a", "a"], ["b", "b"])
    assert precision == 0.0
    assert recall == 0.0
    assert macro_precision_recall([], []) == (0.0, 0.0)
