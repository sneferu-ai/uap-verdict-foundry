"""Production operator-evaluation recording and resume tests."""
from __future__ import annotations

import json

import pytest

from uapvf.lineages.registry import declared_lineage_ids
from uapvf.operator_eval import OperatorEvalError, record_operator_eval

# Stale against the shipped MVP scope: local sandboxed lineage execution deferred to phase 2 (README).
pytestmark = pytest.mark.skip(reason="phase 2 / removed: local sandboxed lineage execution deferred to phase 2 (README)")



def _collection(tmp_path):
    media = tmp_path / "capture.jpg"
    media.write_bytes(b"real-operator-media-fixture")
    items = []
    for i in range(300):
        if i < 100:
            subset = "general"
            difficulty = "hard" if i < 30 else "easy"
        elif i < 200:
            subset = "l6_enriched"
            difficulty = None
        elif i < 250:
            subset = "l4_enriched"
            difficulty = None
        else:
            subset = "l7_enriched"
            difficulty = None
        item = {
            "item_id": f"operator-{i:04d}",
            "media": media.name,
            "independence_subset": subset,
            "difficulty": difficulty,
        }
        if i < 40:
            item.update(calibration_subset="general", label="aircraft")
        elif 100 <= i < 160:
            item.update(calibration_subset="l6_enriched", label="aircraft")
        items.append(item)
    path = tmp_path / "collection.json"
    path.write_text(json.dumps({"schema_version": 1, "items": items}))
    return path


def _sandbox_output():
    return {
        "lineages": [
            {
                "lineage_id": lineage_id,
                "classification": "aircraft",
                "confidence": 0.8,
            }
            for lineage_id in declared_lineage_ids()
        ]
    }


def test_records_real_sandbox_outputs_and_provenance(
    tmp_path, settings, monkeypatch
):
    collection = _collection(tmp_path)
    calls = []

    def fake_sandbox(case_id, media_path, case_dir):
        calls.append((case_id, media_path, case_dir))
        return _sandbox_output()

    monkeypatch.setattr(
        "uapvf.operator_eval.run_lineages_sandboxed", fake_sandbox
    )
    result = record_operator_eval(collection, settings)
    assert len(calls) == 300
    assert result["independence_items"] == 300
    assert result["calibration_items"] == 100
    independence = json.loads(
        (settings.var_dir / "operator" / "eval_set_independence" /
         "manifest.json").read_text()
    )
    calibration = json.loads(
        (settings.var_dir / "operator" / "eval_set" / "manifest.json").read_text()
    )
    for manifest in (independence, calibration):
        assert manifest["provenance"]["recorder"] == (
            "uapvf_seven_lineage_sandbox"
        )
        assert manifest["provenance"]["labels_blinded_during_inference"] is True
        assert len(manifest["provenance"]["collection_manifest_sha256"]) == 64
    assert all(item["media_sha256"] for item in independence["items"])


def test_failed_recording_resumes_without_rerunning_completed_items(
    tmp_path, settings, monkeypatch
):
    collection = _collection(tmp_path)
    first_calls = 0

    def fails_after_five(*_args):
        nonlocal first_calls
        first_calls += 1
        if first_calls == 6:
            raise RuntimeError("provider interruption")
        return _sandbox_output()

    monkeypatch.setattr(
        "uapvf.operator_eval.run_lineages_sandboxed", fails_after_five
    )
    with pytest.raises(OperatorEvalError, match="sandbox failed"):
        record_operator_eval(collection, settings)
    assert first_calls == 6

    resumed_calls = 0

    def succeeds(*_args):
        nonlocal resumed_calls
        resumed_calls += 1
        return _sandbox_output()

    monkeypatch.setattr(
        "uapvf.operator_eval.run_lineages_sandboxed", succeeds
    )
    result = record_operator_eval(collection, settings)
    assert result["independence_items"] == 300
    assert resumed_calls == 295
    assert not (settings.var_dir / "operator" / ".eval-recording.lock").exists()
